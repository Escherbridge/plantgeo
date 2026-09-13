import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * `/feed`'s half of FR-4 (OQ-5 pulled forward, not deferred).
 *
 * The row shows the SAME `InterventionLikeButton` the map detail modal shows,
 * and its comment affordance opens the SAME Phase 3 detail modal through the
 * same store -- there is no second comment UI to drift.
 */

const mocks = vi.hoisted(() => ({
  session: { data: null as unknown, status: "authenticated" as string },
  listProposedQuery: vi.fn(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/components/intervention/InterventionLikeButton", () => ({
  InterventionLikeButton: ({ featureId }: { featureId: string }) => (
    <div data-testid="like-button-stub" data-feature-id={featureId} />
  ),
}));
vi.mock("@/components/intervention/InterventionCommentThread", () => ({
  InterventionCommentThread: ({ featureId }: { featureId: string }) => (
    <div data-testid="comment-thread-stub" data-feature-id={featureId} />
  ),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      listProposed: { useQuery: mocks.listProposedQuery },
      getInterventionDetail: {
        useQuery: () => ({ data: undefined, isLoading: true, isError: false }),
      },
    },
  },
}));

import { InterventionFeed } from "@/app/feed/InterventionFeed";
import { useInterventionDetailStore } from "@/stores/intervention-detail-store";

const PROPOSAL = {
  id: "66666666-6666-4666-8666-666666666666",
  name: "Ridge replanting",
  type: "reforestation",
  description: "Two hundred acres of mixed conifer.",
  longitude: -116.2,
  latitude: 43.6,
  createdAt: "2026-09-01T00:00:00.000Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  useInterventionDetailStore.getState().close();
  mocks.session = {
    data: { user: { id: "user-1" } },
    status: "authenticated",
  };
  mocks.listProposedQuery.mockReturnValue({
    data: [PROPOSAL],
    isPending: false,
    error: null,
  });
});

afterEach(() => {
  cleanup();
  useInterventionDetailStore.getState().close();
});

describe("InterventionFeed social row (FR-4)", () => {
  it("shows the shared like control on every row, keyed by the proposal id", () => {
    render(<InterventionFeed />);

    expect(
      screen.getByTestId("like-button-stub").getAttribute("data-feature-id")
    ).toBe(PROPOSAL.id);
  });

  it("opens the same detail modal the map uses, rather than a second comment UI", () => {
    render(<InterventionFeed />);

    expect(screen.queryByTestId("intervention-detail-modal")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: `Comments on ${PROPOSAL.name}` })
    );

    expect(useInterventionDetailStore.getState().featureId).toBe(PROPOSAL.id);
    expect(screen.getByTestId("intervention-detail-modal")).toBeTruthy();
    expect(
      screen.getByTestId("comment-thread-stub").getAttribute("data-feature-id")
    ).toBe(PROPOSAL.id);
  });

  it("keeps the whole feed, social controls included, behind the existing sign-in gate", () => {
    mocks.session = { data: null, status: "unauthenticated" };
    render(<InterventionFeed />);

    expect(screen.getByText("Sign in required")).toBeTruthy();
    expect(screen.queryByTestId("like-button-stub")).toBeNull();
    expect(screen.queryByTestId("comment-thread-stub")).toBeNull();
  });
});
