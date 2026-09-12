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
  vi.fn((..._args: unknown[]): { data: unknown; isLoading?: boolean; error?: { message: string; data?: { code: string } }; refetch?: () => void } => ({ data: [] }))
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
    interventions: {
      listMySubmissions: { useQuery: listMySubmissionsQuery },
      submitIntervention: { useMutation: () => ({ mutate: vi.fn(), isPending: false }) },
      reviseIntervention: { useMutation: () => ({ mutate: vi.fn(), isPending: false }) },
    },
  },
}));

import { CommunityDetails } from "@/components/panels/CommunityDetails";
vi.mock("next-auth/react", () => ({ useSession: () => ({ data: { user: { id: "current-contributor" } } }) }));
import { useAuthStore } from "@/stores/auth-store";

const INITIAL_AUTH_STATE = useAuthStore.getState();

beforeEach(() => {
  useAuthStore.setState(INITIAL_AUTH_STATE, true);
  listMySubmissionsQuery.mockReturnValue({ data: [] });
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
  it("keeps loading and service errors distinct from an empty ledger", () => {
    listMySubmissionsQuery.mockReturnValue({ data: undefined, isLoading: true });
    const view = renderPanel();
    expect(screen.getByText("Loading recommendations…")).toBeTruthy();
    expect(screen.queryByText("You have not recommended any interventions yet.")).toBeNull();
    view.unmount();
    listMySubmissionsQuery.mockReturnValue({ data: undefined, error: { message: "Database timeout", data: { code: "INTERNAL_SERVER_ERROR" } } });
    renderPanel();
    expect(screen.getByText(/Recommendations could not be loaded/)).toBeTruthy();
    expect(screen.queryByText("You have not recommended any interventions yet.")).toBeNull();
  });

  it.each([
    ["UNAUTHORIZED", "Sign in to view your recommendations."],
    ["FORBIDDEN", "You do not have access to recommendations in this workspace."],
  ])("names %s access failures", (code, message) => {
    listMySubmissionsQuery.mockReturnValue({ data: undefined, error: { message: "Request failed", data: { code } } });
    renderPanel();
    expect(screen.getByText(message)).toBeTruthy();
  });

  it("names missing layer provisioning without claiming no submissions exist", () => {
    listMySubmissionsQuery.mockReturnValue({ data: undefined, error: { message: "The interventions layer is not provisioned", data: { code: "PRECONDITION_FAILED" } } });
    renderPanel();
    expect(screen.getByText("The interventions layer has not been provisioned in this environment.")).toBeTruthy();
  });

  it("offers resubmission only to the original contributor", () => {
    listMySubmissionsQuery.mockReturnValue({ data: [
      { id: "own", status: "revision_requested", properties: { name: "Own site", type: "biochar", submittedByUserId: "current-contributor" }, reviewNote: "Adjust the boundary" },
      { id: "other", status: "rejected", properties: { name: "Another site", type: "biochar", submittedByUserId: "other-contributor" }, reviewNote: "Not suitable" },
    ] });
    renderPanel();
    expect(screen.getAllByRole("button", { name: "Edit and resubmit" })).toHaveLength(1);
    expect(screen.getByText("Reviewer note: Adjust the boundary")).toBeTruthy();
  });

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

  it("labels a published row as published", () => {
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
    expect(screen.getByText(/Published/)).toBeTruthy();
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
