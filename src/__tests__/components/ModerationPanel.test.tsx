import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * ModerationPanel is the post-publication lifecycle manager on /moderation. tRPC is stubbed
 * rather than driven over a link -- the server-side state machine has its own tests in
 * src/__tests__/trpc/interventions.test.ts; what is under test here is that the panel offers
 * only the moves the server listed, sends the stage the reader saw, and explains a refusal.
 */
const mocks = vi.hoisted(() => ({
  listLifecycleQuery: vi.fn(),
  invalidate: vi.fn(),
  transitionMutate: vi.fn(),
  transitionError: null as { data: { code: string } } | null,
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({
      interventions: { listLifecycle: { invalidate: mocks.invalidate } },
    }),
    interventions: {
      listLifecycle: { useQuery: mocks.listLifecycleQuery },
      transitionLifecycleState: {
        useMutation: (opts?: { onSuccess?: () => void; onError?: (error: { data: { code: string } }) => void }) => ({
          mutate: (input: unknown) => {
            mocks.transitionMutate(input);
            if (mocks.transitionError) opts?.onError?.(mocks.transitionError);
            else opts?.onSuccess?.();
          },
          isPending: false,
        }),
      },
    },
  },
}));

import { ModerationPanel } from "@/components/panels/ModerationPanel";

/** Exactly what listLifecycle projects, including the server-computed moves. */
const PUBLISHED = [
  { id: "11111111-1111-4111-8111-111111111111", name: "Ridge silvopasture plot", type: "silvopasture", stage: "published", fromStage: "published", nextStages: ["active"], longitude: -116.2, latitude: 43.6, updatedAt: "2026-10-01T00:00:00Z" },
  { id: "22222222-2222-4222-8222-222222222222", name: "Creek riparian buffer", type: "riparian_buffer", stage: "active", fromStage: "active", nextStages: ["monitored"], longitude: -116.3, latitude: 43.7, updatedAt: "2026-10-02T00:00:00Z" },
  { id: "33333333-3333-4333-8333-333333333333", name: "Terraced swale", type: "swale", stage: "monitored", fromStage: "monitored", nextStages: [], longitude: -116.4, latitude: 43.8, updatedAt: "2026-10-03T00:00:00Z" },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.transitionError = null;
  mocks.listLifecycleQuery.mockReturnValue({ data: PUBLISHED, isLoading: false, error: null });
});

describe("ModerationPanel lifecycle controls", () => {
  it("offers exactly the server's next stages and none for a finished intervention", () => {
    renderWithProviders(<ModerationPanel />);

    expect(screen.getAllByRole("button", { name: "Mark active" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Mark monitored" })).toHaveLength(1);
    expect(screen.getByText("No further lifecycle stage.")).toBeTruthy();
    // Publication is the review queue's job; this panel never offers it.
    expect(screen.queryByRole("button", { name: /publish|approve|reject/i })).toBeNull();
  });

  it("sends the row's own id with the stage the reader saw and the stage clicked", () => {
    renderWithProviders(<ModerationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Mark monitored" }));

    expect(mocks.transitionMutate).toHaveBeenCalledWith({
      interventionId: PUBLISHED[1].id,
      fromStage: "active",
      toStage: "monitored",
    });
    expect(mocks.invalidate).toHaveBeenCalledTimes(1);
  });

  it("explains a CONFLICT as a stage that moved on and refreshes the list", () => {
    mocks.transitionError = { data: { code: "CONFLICT" } };
    renderWithProviders(<ModerationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Mark active" }));

    expect(screen.getByRole("alert").textContent).toMatch(/stage changed before your update/);
    expect(mocks.invalidate).toHaveBeenCalledTimes(1);
  });

  it("says when nothing is published rather than drawing an empty list", () => {
    mocks.listLifecycleQuery.mockReturnValue({ data: [], isLoading: false, error: null });
    renderWithProviders(<ModerationPanel />);

    expect(screen.getByText("No published interventions yet.")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
