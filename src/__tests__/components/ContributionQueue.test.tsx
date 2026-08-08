import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * next/link needs the App Router context to mount, which nothing here stands up. The
 * assertion below is about the href the queue builds, so a plain anchor is the honest
 * double: it preserves exactly the contract under test and nothing else.
 */
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

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

/** The shape listPendingReview projects: identity plus the properties bag a reviewer reads. */
const PENDING_FEATURE = {
  id: "feature-1",
  layerId: "layer-uuid",
  layerName: "interventions",
  status: "pending_review",
  createdAt: "2026-08-04T00:00:00Z",
  properties: {
    name: "Ridge silvopasture plot",
    type: "silvopasture",
    description: "South-facing slope above the creek.",
    geometry: { type: "Point", coordinates: [-116.2023, 43.6150] },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listPendingReviewQuery.mockReturnValue({
    data: [PENDING_FEATURE],
    isLoading: false,
    error: undefined,
  });
});

// The bug this pins: the row rendered a feature UUID and a layer UUID and nothing else, so
// a reviewer approving a submission onto the public map could not tell what it was or where
// it was. Everything asserted here is already carried by listPendingReview's projection.
describe("ContributionQueue row identification", () => {
  it("names the submission, its category, its layer and when it arrived", () => {
    renderWithProviders(<ContributionQueue />);

    expect(screen.getByText("Ridge silvopasture plot")).toBeTruthy();
    expect(screen.getByText(/Silvopasture/)).toBeTruthy();
    expect(screen.getByText(/interventions/)).toBeTruthy();
    expect(screen.getByText("South-facing slope above the creek.")).toBeTruthy();
  });

  it("links the location to the map's focus deep link instead of printing a UUID", () => {
    renderWithProviders(<ContributionQueue />);

    const link = screen.getByRole("link", { name: /43\.6150, -116\.2023/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toContain("focusLng=-116.202300");
    expect(link.getAttribute("href")).toContain("focusLat=43.615000");
    expect(screen.queryByText("feature-1")).toBeNull();
  });

  it("says so plainly when a submission carries no geometry", () => {
    mocks.listPendingReviewQuery.mockReturnValue({
      data: [{ ...PENDING_FEATURE, properties: { name: "Locationless" } }],
      isLoading: false,
      error: undefined,
    });
    renderWithProviders(<ContributionQueue />);

    expect(screen.getByText("No location on this submission")).toBeTruthy();
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
