import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * The bug this pins (RUNBOOK P0, 2026-09-02): the queue hard-coded tau_est = 0.18 with a
 * [11%, 25%] interval inside the render loop and labelled it a causal benefit score, beside
 * the approve/reject/lifecycle controls. No evaluated result backs any of those numbers.
 * tRPC is stubbed rather than driven over a link -- what is under test is the RENDERING and
 * the mutation WIRING, same rationale as ContributionQueue.test.tsx.
 */
const mocks = vi.hoisted(() => ({
  listProposedQuery: vi.fn(),
  invalidate: vi.fn(),
  castVoteMutate: vi.fn(),
  transitionMutate: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({
      interventions: { listProposed: { invalidate: mocks.invalidate } },
    }),
    interventions: {
      listProposed: { useQuery: mocks.listProposedQuery },
      castModerationVote: {
        useMutation: () => ({ mutate: mocks.castVoteMutate, isPending: false }),
      },
      transitionLifecycleState: {
        useMutation: () => ({ mutate: mocks.transitionMutate, isPending: false }),
      },
    },
  },
}));

import { ModerationPanel } from "@/components/panels/ModerationPanel";

/** Exactly what listProposed projects: identity, properties bag fields, centroid, createdAt. */
const PROPOSALS = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Ridge silvopasture plot",
    type: "silvopasture",
    description: "South-facing slope above the creek.",
    longitude: -116.2023,
    latitude: 43.615,
    createdAt: "2026-08-04T00:00:00Z",
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Creek riparian buffer",
    type: "riparian_buffer",
    description: "Bank stabilisation along the lower reach.",
    longitude: -116.35,
    latitude: 43.72,
    createdAt: "2026-08-05T00:00:00Z",
  },
];

const ABSENCE_NOTICE = "No evaluated effect estimate is available for this proposal";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listProposedQuery.mockReturnValue({ data: PROPOSALS, isLoading: false });
});

describe("ModerationPanel effect evidence", () => {
  it("shows no percentage, interval or causal-benefit label anywhere", () => {
    const { container } = renderWithProviders(<ModerationPanel />);
    const rendered = container.textContent ?? "";

    expect(rendered).not.toContain("%");
    expect(rendered).not.toContain("[");
    expect(screen.queryByText(/causal benefit/i)).toBeNull();
    expect(screen.queryByText(/tau/i)).toBeNull();
  });

  it("states the absence once per proposal instead of standing in a number", () => {
    renderWithProviders(<ModerationPanel />);

    expect(screen.getAllByText(ABSENCE_NOTICE)).toHaveLength(PROPOSALS.length);
    expect(
      screen.getAllByText(/provenance-carrying, time-honest evaluation/)
    ).toHaveLength(PROPOSALS.length);
  });
});

/**
 * The two states that are not a populated queue. Both matter for the same reason the effect-
 * evidence cases above do: this panel sits next to approve/reject controls, and a reader must be
 * able to tell "still loading" from "reviewed everything" from "one proposal is waiting".
 */
describe("ModerationPanel queue states", () => {
  it("says it is loading, and offers no control while it does not know the queue", () => {
    mocks.listProposedQuery.mockReturnValue({ data: undefined, isLoading: true });
    renderWithProviders(<ModerationPanel />);

    expect(screen.getByText(/Loading Expert Moderation Queue/)).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /Approve & Publish/ })).toHaveLength(0);
    // A pending count drawn from an unknown queue would read as "0 waiting" -- an answer.
    expect(screen.queryByText(/Pending Items/)).toBeNull();
  });

  it("says the queue is empty rather than drawing an empty list", () => {
    mocks.listProposedQuery.mockReturnValue({ data: [], isLoading: false });
    renderWithProviders(<ModerationPanel />);

    expect(
      screen.getByText("No proposals awaiting expert moderation review.")
    ).toBeTruthy();
    expect(screen.getByText(/0 Pending Items/)).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /Approve & Publish/ })).toHaveLength(0);
    expect(screen.queryByText(ABSENCE_NOTICE)).toBeNull();
  });

  it("counts the pending items it is showing", () => {
    renderWithProviders(<ModerationPanel />);

    expect(screen.getByText(`${PROPOSALS.length} Pending Items`)).toBeTruthy();
    expect(screen.queryByText("No proposals awaiting expert moderation review.")).toBeNull();
  });
});

describe("ModerationPanel control wiring", () => {
  it("casts an approve vote for the row's own intervention id", () => {
    renderWithProviders(<ModerationPanel />);

    const approveButtons = screen.getAllByRole("button", { name: /Approve & Publish/ });
    expect(approveButtons).toHaveLength(PROPOSALS.length);
    fireEvent.click(approveButtons[0]);

    expect(mocks.castVoteMutate).toHaveBeenCalledWith({
      interventionId: PROPOSALS[0].id,
      vote: "approve",
      note: undefined,
    });
  });

  it("transitions the row's own intervention to the active lifecycle state", () => {
    renderWithProviders(<ModerationPanel />);

    fireEvent.click(screen.getAllByRole("button", { name: /Set ACTIVE/ })[1]);

    expect(mocks.transitionMutate).toHaveBeenCalledWith({
      interventionId: PROPOSALS[1].id,
      targetState: "active",
    });
  });
});
