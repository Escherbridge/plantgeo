import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * The tRPC hooks are stubbed rather than driven over a link -- this asserts the
 * WIRING (reject requires a note, approve does not) not the network layer.
 */
const mocks = vi.hoisted(() => ({
  listPendingReviewQuery: vi.fn(),
  invalidate: vi.fn(),
  publishMutate: vi.fn(),
  rejectMutate: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({
      contributions: { listPendingReview: { invalidate: mocks.invalidate } },
    }),
    contributions: {
      listPendingReview: { useQuery: mocks.listPendingReviewQuery },
      publishContribution: {
        useMutation: () => ({
          mutate: mocks.publishMutate,
          isPending: false,
        }),
      },
      rejectContribution: {
        useMutation: (opts?: { onSuccess?: (data: unknown, variables: unknown) => void }) => ({
          mutate: (input: { featureId: string; reviewNote: string }) => {
            mocks.rejectMutate(input);
            opts?.onSuccess?.(undefined, input);
          },
          isPending: false,
        }),
      },
    },
  },
}));

import { ContributionQueue } from "@/components/panels/ContributionQueue";

const PENDING_FEATURE = { id: "feature-1", layerId: "layer-uuid", status: "pending_review" };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listPendingReviewQuery.mockReturnValue({
    data: [PENDING_FEATURE],
    isLoading: false,
    error: undefined,
  });
});

describe("ContributionQueue reject-note requirement", () => {
  it("disables Reject until the note has non-whitespace content", () => {
    renderWithProviders(<ContributionQueue />);
    const rejectButton = screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement;
    expect(rejectButton.disabled).toBe(true);

    const input = screen.getByPlaceholderText("Rejection note (required to reject)");
    fireEvent.change(input, { target: { value: "   " } });
    expect(rejectButton.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "Geometry overlaps a protected area" } });
    expect(rejectButton.disabled).toBe(false);
  });

  it("submits the trimmed note as reviewNote when rejecting", () => {
    renderWithProviders(<ContributionQueue />);
    const input = screen.getByPlaceholderText("Rejection note (required to reject)");
    fireEvent.change(input, { target: { value: "  Not viable here  " } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(mocks.rejectMutate).toHaveBeenCalledWith({
      featureId: "feature-1",
      reviewNote: "Not viable here",
    });
  });

  it("never sends an empty or whitespace-only reviewNote, even by direct click attempts", () => {
    renderWithProviders(<ContributionQueue />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(mocks.rejectMutate).not.toHaveBeenCalled();
  });

  it("does not require a note to approve", () => {
    renderWithProviders(<ContributionQueue />);
    const approveButton = screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement;
    expect(approveButton.disabled).toBe(false);

    fireEvent.click(approveButton);
    expect(mocks.publishMutate).toHaveBeenCalledWith({ featureId: "feature-1" });
  });
});
