import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LandContextResult } from "@/lib/environmental/land-context-contract";
import { useLandContextStore } from "@/stores/land-context-store";

const query = vi.hoisted(() => ({
  data: undefined as unknown,
  isError: false,
  isFetching: false,
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: { landContext: { lookupContactsForSelection: { useQuery: () => query } } },
}));

import { LandContextPanelHost } from "@/components/panels/land-context/LandContextPanelHost";

const boundary: LandContextResult = {
  coverageState: "matched",
  sourceFeature: {
    sourceNamespace: "blm-test",
    nativeFeatureKey: "surface-record",
    nativeFeatureVersion: null,
    familyType: "blm_surface_management",
    interestType: "surface_management",
    state: "OR",
    county: null,
    geometryWkb: null,
  },
  sourceRelease: {
    publisher: "Bureau of Land Management",
    canonicalEndpoint: "https://www.blm.gov/",
    sourceVersion: "review-boundary-release",
    captureTime: null,
    sourceEffectiveTime: null,
    sourcePublishedTime: null,
    admissionVerdict: "admitted",
  },
  matchedRegionOrOverlap: { kind: "point_containment", description: "The point is inside this boundary." },
  organizationOffice: null,
  route: null,
  roleOrRouteType: null,
  assignmentEvidence: null,
  publicContactUrl: null,
  verificationTime: null,
  documentedHelp: null,
  unresolvedGaps: [],
  isCurrentReferenceOnly: true,
};

beforeEach(() => {
  query.data = undefined;
  query.isError = false;
  query.isFetching = false;
  useLandContextStore.setState({
    ...useLandContextStore.getInitialState(),
    selection: { mode: "point", point: [-122, 44] },
    results: [{
      id: "blm-test:surface-record",
      group: "blm-lands",
      title: "Published boundary",
      geometry: { type: "GeometryCollection", geometries: [] },
      evidence: boundary,
    }],
    candidateIndex: 0,
    panelOpen: true,
    queryStatus: "settled",
  });
});

afterEach(() => {
  cleanup();
  useLandContextStore.setState(useLandContextStore.getInitialState());
});

function expectBoundaryEvidence(): void {
  expect(screen.getByRole("heading", { name: "Place details" })).toBeTruthy();
  expect(screen.getByText("Version: review-boundary-release")).toBeTruthy();
  expect(screen.queryByText("No published office/program route resolved for this selection.")).toBeNull();
}

describe("land-contact lookup state beside the selected boundary", () => {
  it("shows loading without replacing the published boundary", () => {
    query.isFetching = true;
    render(<LandContextPanelHost />);
    expect(screen.getByRole("status").textContent).toContain("Reading published office contacts");
    expectBoundaryEvidence();
  });

  it("shows a transport failure without claiming that no office exists", () => {
    query.isError = true;
    render(<LandContextPanelHost />);
    expect(screen.getByRole("status").textContent).toContain("Office contacts could not be read");
    expectBoundaryEvidence();
  });

  it.each([
    ["upstream_unavailable", "The office jurisdiction request timed out.", "Office contacts could not be read."],
    ["unknown_coverage", "No published office jurisdiction matched this selection.", "Office contact coverage is incomplete or unavailable."],
  ] as const)("retains %s and the reader's explanation", (coverageState, gap, message) => {
    query.data = {
      status: "ok",
      data: [{ ...boundary, coverageState, sourceFeature: null, sourceRelease: null, unresolvedGaps: [gap] }],
    };
    render(<LandContextPanelHost />);
    expect(screen.getByRole("status").textContent).toContain(message);
    expect(screen.getByRole("status").textContent).toContain(gap);
    expectBoundaryEvidence();
  });

  it("explains a refused contact lookup while keeping boundary evidence", () => {
    query.data = { status: "budget_exceeded", reason: "response_bytes_would_exceed_limit", limit: 2_000_000, requested: 2_100_000 };
    render(<LandContextPanelHost />);
    expect(screen.getByRole("status").textContent).toContain("The office lookup exceeded its area or response limit");
    expectBoundaryEvidence();
  });
});
