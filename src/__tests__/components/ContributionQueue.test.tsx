import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
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
  revisionMutate: vi.fn(),
  refreshTiles: vi.fn(),
  refetch: vi.fn(),
  publishSuccess: undefined as undefined | (() => Promise<void>),
  publishError: undefined as undefined | ((error: { message: string; data?: { code?: string } }, variables: { featureId: string }) => Promise<void>),
}));

vi.mock("@/lib/map/intervention-publication", () => ({ notifyInterventionPublication: mocks.refreshTiles }));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({
      contributions: { listPendingReview: { invalidate: mocks.invalidate } },
      interventions: { listMySubmissions: { invalidate: mocks.invalidate }, listProposed: { invalidate: mocks.invalidate } },
      wildfire: { getInterventions: { invalidate: mocks.invalidate } },
    }),
    contributions: {
      listPendingReview: { useQuery: mocks.listPendingReviewQuery },
      publishContribution: {
        useMutation: (opts: { onSuccess: () => Promise<void>; onError: NonNullable<typeof mocks.publishError> }) => {
          mocks.publishSuccess = opts.onSuccess;
          mocks.publishError = opts.onError;
          return { mutate: mocks.publishMutate, isPending: false };
        },
      },
      requestRevisionContribution: { useMutation: () => ({ mutate: mocks.revisionMutate, isPending: false }) },
      rejectContribution: {
        useMutation: (opts?: { onSuccess?: (data: unknown, variables: unknown) => void }) => ({
          mutate: (input: { featureId: string; expectedReviewVersion: string; reviewNote: string }) => {
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
  reviewVersion: "a".repeat(64),
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
    refetch: mocks.refetch,
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

    const input = screen.getByPlaceholderText("Review note (required to reject or request revision)");
    fireEvent.change(input, { target: { value: "   " } });
    expect(rejectButton.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "Geometry overlaps a protected area" } });
    expect(rejectButton.disabled).toBe(false);
  });

  it("submits the trimmed note as reviewNote when rejecting", () => {
    renderWithProviders(<ContributionQueue />);
    const input = screen.getByPlaceholderText("Review note (required to reject or request revision)");
    fireEvent.change(input, { target: { value: "  Not viable here  " } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(mocks.rejectMutate).toHaveBeenCalledWith({
      featureId: "feature-1",
      expectedReviewVersion: PENDING_FEATURE.reviewVersion,
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
    const approveButton = screen.getByRole("button", { name: "Approve & Publish" }) as HTMLButtonElement;
    expect(approveButton.disabled).toBe(false);

    fireEvent.click(approveButton);
    expect(mocks.publishMutate).toHaveBeenCalledWith({ featureId: "feature-1", expectedReviewVersion: PENDING_FEATURE.reviewVersion });
  });
});


describe("ContributionQueue publication and truthful states", () => {
  it("does not offer intervention revision for a generic observation", () => {
    mocks.listPendingReviewQuery.mockReturnValue({ data: [{ ...PENDING_FEATURE, layerName: "observations" }], isLoading: false });
    renderWithProviders(<ContributionQueue />);
    expect(screen.queryByRole("button", { name: "Request revision" })).toBeNull();
  });
  it("requires individual review before recovering a legacy approval", () => {
    mocks.listPendingReviewQuery.mockReturnValue({ data: [{ ...PENDING_FEATURE, status: "approved" }], isLoading: false });
    renderWithProviders(<ContributionQueue />);
    const publish = screen.getByRole("button", { name: "Publish reviewed legacy approval" }) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "  Verified original author, consent and boundary  " } });
    fireEvent.click(publish);
    expect(mocks.publishMutate).toHaveBeenCalledWith({ featureId: "feature-1", expectedReviewVersion: PENDING_FEATURE.reviewVersion, recoveryReviewNote: "Verified original author, consent and boundary" });
    expect(screen.queryByRole("button", { name: "Request revision" })).toBeNull();
  });

  it("requests an actionable revision without publishing", () => {
    renderWithProviders(<ContributionQueue />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "  Remove the roadway  " } });
    fireEvent.click(screen.getByRole("button", { name: "Request revision" }));
    expect(mocks.revisionMutate).toHaveBeenCalledWith({ featureId: "feature-1", expectedReviewVersion: PENDING_FEATURE.reviewVersion, reviewNote: "Remove the roadway" });
    expect(mocks.publishMutate).not.toHaveBeenCalled();
  });

  it("refreshes intervention tiles and contributor queries after publication", async () => {
    renderWithProviders(<ContributionQueue />);
    await act(async () => { await mocks.publishSuccess?.(); });
    expect(mocks.refreshTiles).toHaveBeenCalledTimes(1);
    expect(mocks.invalidate).toHaveBeenCalledTimes(4);
  });

  it("refreshes a stale review and clears its old note before the moderator decides again", async () => {
    const view = renderWithProviders(<ContributionQueue />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Reviewed original boundary" } });
    await act(async () => {
      await mocks.publishError?.({ message: "This submission changed since you opened it. Review the refreshed boundary before deciding.", data: { code: "CONFLICT" } }, { featureId: "feature-1" });
    });
    expect(mocks.refetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert").textContent).toContain("Review the refreshed boundary");
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("");
    expect(mocks.refreshTiles).not.toHaveBeenCalled();
    mocks.listPendingReviewQuery.mockReturnValue({ data: [{ ...PENDING_FEATURE, reviewVersion: "b".repeat(64) }], isLoading: false, refetch: mocks.refetch });
    view.rerender(<ContributionQueue />);
    fireEvent.click(screen.getByRole("button", { name: "Approve & Publish" }));
    expect(mocks.publishMutate).toHaveBeenCalledWith({ featureId: "feature-1", expectedReviewVersion: "b".repeat(64) });
  });

  it.each([
    ["UNAUTHORIZED", "Sign in to review contributions."],
    ["FORBIDDEN", "Only experts and administrators can review contributions."],
    ["PRECONDITION_FAILED", "Community review is not provisioned in this environment."],
    ["INTERNAL_SERVER_ERROR", "The review service is unavailable. Your queue could not be loaded."],
  ])("distinguishes %s from an empty queue", (code, message) => {
    mocks.listPendingReviewQuery.mockReturnValue({ error: { data: { code } }, isLoading: false, refetch: vi.fn() });
    renderWithProviders(<ContributionQueue />);
    expect(screen.getByRole("alert").textContent).toContain(message);
    expect(screen.queryByText(/No pending contributions/)).toBeNull();
  });

  it("shows loading independently from confirmed absence", () => {
    mocks.listPendingReviewQuery.mockReturnValue({ isLoading: true });
    renderWithProviders(<ContributionQueue />);
    expect(screen.getByText("Loading...")).toBeTruthy();
    expect(screen.queryByText(/No pending contributions/)).toBeNull();
  });
});
