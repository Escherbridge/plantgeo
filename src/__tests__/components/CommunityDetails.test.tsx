import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * Phase 4 (community_engagement_completion_20260805): listMySubmissions already
 * returns status/reviewNote per row -- this asserts the RENDERING, that a
 * submitter sees pending / published / rejected and the reviewer's note on
 * rejection. tRPC is stubbed rather than driven over a link, same rationale as
 * TimeSliderCapabilitiesLoader.test.tsx.
 */
const listMySubmissionsQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => ({ data: undefined as unknown }))
);
const listMyTeamsQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => ({ data: [] as unknown[] }))
);
const getRequestsQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => ({ data: [] as unknown[], error: null, refetch: vi.fn() }))
);

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    teams: { listMyTeams: { useQuery: listMyTeamsQuery } },
    community: { getRequests: { useQuery: getRequestsQuery } },
    interventions: { listMySubmissions: { useQuery: listMySubmissionsQuery } },
  },
}));

import { CommunityDetails } from "@/components/panels/CommunityDetails";
import { useAuthStore } from "@/stores/auth-store";

const INITIAL_AUTH_STATE = useAuthStore.getState();

beforeEach(() => {
  useAuthStore.setState(INITIAL_AUTH_STATE, true);
  listMySubmissionsQuery.mockReturnValue({ data: undefined });
  listMyTeamsQuery.mockReturnValue({ data: [] });
  getRequestsQuery.mockReturnValue({ data: [], error: null, refetch: vi.fn() });
});

afterEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState(INITIAL_AUTH_STATE, true);
});

function renderPanel() {
  return renderWithProviders(
    <CommunityDetails mapCenter={{ lat: 43.6, lon: -116.2 }} />
  );
}

describe("CommunityDetails submission status", () => {
  it("shows an empty state before any recommendation has been submitted", () => {
    renderPanel();

    expect(
      screen.getByText("You have not recommended any interventions yet.")
    ).toBeTruthy();
  });

  it("labels a pending_review row as in review, with no reviewer note", () => {
    listMySubmissionsQuery.mockReturnValue({
      data: [
        {
          id: "sub-pending",
          properties: { name: "Ridge silvopasture plot", type: "silvopasture" },
          status: "pending_review",
          reviewNote: null,
          createdAt: "2026-08-04T00:00:00Z",
          updatedAt: "2026-08-04T00:00:00Z",
        },
      ],
    });
    renderPanel();

    expect(screen.getByText("Ridge silvopasture plot")).toBeTruthy();
    expect(screen.getByText(/In review/)).toBeTruthy();
    expect(screen.queryByText(/Reviewer note:/)).toBeNull();
  });

  it("labels a published row as approved", () => {
    listMySubmissionsQuery.mockReturnValue({
      data: [
        {
          id: "sub-published",
          properties: { name: "Creekside keyline berm", type: "keyline" },
          status: "published",
          reviewNote: null,
          createdAt: "2026-08-04T00:00:00Z",
          updatedAt: "2026-08-04T00:00:00Z",
        },
      ],
    });
    renderPanel();

    expect(screen.getByText("Creekside keyline berm")).toBeTruthy();
    expect(screen.getByText(/Approved/)).toBeTruthy();
    expect(screen.queryByText(/Reviewer note:/)).toBeNull();
  });

  it("labels a rejected row as not accepted and shows the reviewer's note", () => {
    listMySubmissionsQuery.mockReturnValue({
      data: [
        {
          id: "sub-rejected",
          properties: { name: "Floodplain biochar site", type: "biochar" },
          status: "rejected",
          reviewNote: "Site overlaps a protected wetland buffer.",
          createdAt: "2026-08-04T00:00:00Z",
          updatedAt: "2026-08-04T00:00:00Z",
        },
      ],
    });
    renderPanel();

    expect(screen.getByText("Floodplain biochar site")).toBeTruthy();
    expect(screen.getByText(/Not accepted/)).toBeTruthy();
    expect(
      screen.getByText("Reviewer note: Site overlaps a protected wetland buffer.")
    ).toBeTruthy();
  });

  it("does not invent a reviewer note when a rejected row somehow has none", () => {
    listMySubmissionsQuery.mockReturnValue({
      data: [
        {
          id: "sub-rejected-blank",
          properties: { name: "Noteless rejection", type: "reforestation" },
          status: "rejected",
          reviewNote: null,
          createdAt: "2026-08-04T00:00:00Z",
          updatedAt: "2026-08-04T00:00:00Z",
        },
      ],
    });
    renderPanel();

    expect(screen.getByText("Noteless rejection")).toBeTruthy();
    expect(screen.getByText(/Not accepted/)).toBeTruthy();
    expect(screen.queryByText(/Reviewer note:/)).toBeNull();
  });
});
