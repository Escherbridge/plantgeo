import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * Phase 4 (community_engagement_completion_20260805): listMySubmissions already
 * returns status/reviewNote per row -- this asserts the RENDERING, that a
 * submitter sees pending / published / rejected and the reviewer's note on
 * rejection. tRPC is stubbed rather than driven over a link, same rationale as
 * TimeSliderCapabilitiesLoader.test.tsx.
 */
const listMySubmissionsQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => {
    const result: { data: unknown; refetch?: () => void } = { data: undefined };
    return result;
  })
);
const listMyTeamsQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => ({ data: [] as unknown[] }))
);
/** Asserted directly by the intervention-drafts-overlay invalidation test below. */
const draftsInvalidateMocks = vi.hoisted(() => ({
  listMySubmissions: vi.fn().mockResolvedValue(undefined),
  listProposed: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    teams: { listMyTeams: { useQuery: listMyTeamsQuery } },
    interventions: { listMySubmissions: { useQuery: listMySubmissionsQuery } },
    useUtils: () => ({
      interventions: {
        listMySubmissions: { invalidate: draftsInvalidateMocks.listMySubmissions },
        listProposed: { invalidate: draftsInvalidateMocks.listProposed },
      },
    }),
  },
}));

/**
 * The real modal drives a live `submitIntervention` mutation; what this file asserts is the
 * WIRING from that mutation's success to the overlay invalidation, so the modal is stubbed down
 * to a button that fires `onSuccess` -- same rationale ContributionQueue.test.tsx uses for its
 * tRPC stubs.
 */
vi.mock("@/components/panels/InterventionSubmitModal", () => ({
  InterventionSubmitModal: ({ onSuccess }: { onSuccess?: () => void }) => (
    <button type="button" onClick={() => onSuccess?.()}>
      Fake submit success
    </button>
  ),
}));

/** Same rationale: the request modal's own wiring is asserted in RequestSubmitModal.test.tsx. */
vi.mock("@/components/panels/RequestSubmitModal", () => ({
  RequestSubmitModal: ({ onSuccess }: { onSuccess?: () => void }) => (
    <button type="button" onClick={() => onSuccess?.()}>
      Fake request success
    </button>
  ),
}));

import { CommunityDetails } from "@/components/panels/CommunityDetails";
import { useAuthStore } from "@/stores/auth-store";

const INITIAL_AUTH_STATE = useAuthStore.getState();

beforeEach(() => {
  useAuthStore.setState(INITIAL_AUTH_STATE, true);
  listMySubmissionsQuery.mockReturnValue({ data: undefined, refetch: vi.fn() });
  listMyTeamsQuery.mockReturnValue({ data: [] });
  draftsInvalidateMocks.listMySubmissions.mockClear();
  draftsInvalidateMocks.listProposed.mockClear();
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

describe("CommunityDetails intervention-drafts overlay invalidation", () => {
  it("invalidates both listMySubmissions and listProposed when a submission succeeds", () => {
    renderPanel();

    fireEvent.click(screen.getByText("+ Recommend"));
    fireEvent.click(screen.getByText("Fake submit success"));

    expect(draftsInvalidateMocks.listMySubmissions).toHaveBeenCalledTimes(1);
    expect(draftsInvalidateMocks.listProposed).toHaveBeenCalledTimes(1);
  });

  it("refreshes the map overlay when a REQUEST succeeds, since a request has no panel list", () => {
    renderPanel();

    fireEvent.click(screen.getByText("+ Request"));
    fireEvent.click(screen.getByText("Fake request success"));

    expect(draftsInvalidateMocks.listMySubmissions).toHaveBeenCalledTimes(1);
    expect(draftsInvalidateMocks.listProposed).toHaveBeenCalledTimes(1);
  });
});

/**
 * Phase 3 of `public_strategy_requests_20260913`. The panel used to render a private,
 * `community.getRequests`-backed list with copy promising the request was never shown on the map.
 * Both the read and the promise are gone; what must survive is the ability to SUBMIT one.
 */
describe("CommunityDetails strategy-request section after the private path was retired", () => {
  it("reads nothing from the deleted community router", () => {
    // The tRPC stub above exposes no `community` namespace at all, so any surviving
    // `trpc.community.*` call would throw on render rather than quietly return undefined.
    expect(() => renderPanel()).not.toThrow();
  });

  it("keeps the + Request entry point", () => {
    renderPanel();

    expect(screen.getByText("+ Request")).toBeTruthy();
  });

  it("points the reader at the map instead of promising privacy", () => {
    const { container } = renderPanel();
    const text = container.textContent ?? "";

    expect(text).toMatch(/appears on the map|on the map/i);
    for (const retired of [
      /private community request/i,
      /never shown on the map/i,
      /requests are private/i,
      /shared only with authenticated members of/i,
      /private location/i,
      /no private strategy requests/i,
    ]) {
      expect(text).not.toMatch(retired);
    }
  });

  it("no longer offers a strategy-type filter over a list it does not render", () => {
    renderPanel();

    expect(screen.queryByText("All Types")).toBeNull();
  });
});
