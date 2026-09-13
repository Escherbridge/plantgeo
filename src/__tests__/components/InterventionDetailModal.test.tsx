import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The social children are stubbed (their own behaviour is pinned in
 * `InterventionLikeButton.test.tsx` / `InterventionCommentThread.test.tsx`);
 * what the container test below asserts is the WIRING -- that both mount, for
 * the resolved record's id.
 */
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
      getInterventionDetail: {
        useQuery: () => ({ data: undefined, isLoading: true, isError: false }),
      },
    },
  },
}));

import {
  InterventionDetailCard,
  InterventionDetailModal,
  type InterventionDetailCardProps,
} from "@/components/map/InterventionDetailModal";
import { useInterventionDetailStore } from "@/stores/intervention-detail-store";
import { INTERVENTION_PENDING_REVIEW_COLOR } from "@/lib/map/layers";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";

/**
 * The standalone detail surface (OQ-3), tested through its presentational half
 * so no tRPC client is needed to assert what a reader sees.
 *
 * The load-bearing assertions: the REAL geometry is drawn (every submitted
 * position, never a centroid pin), the status pill speaks the same
 * orange-for-in-review language the map paints with, the reviewer note appears
 * on exactly one status, and the compact card expands to a full-viewport
 * lightbox and back.
 */

const POLYGON: GeoJSON.Geometry = {
  type: "Polygon",
  coordinates: [
    [
      [-120.1, 46.1],
      [-120.0, 46.1],
      [-120.0, 46.2],
      [-120.1, 46.2],
      [-120.1, 46.1],
    ],
  ],
};

const RECORD: InterventionDetailRecord = {
  id: "66666666-6666-4666-8666-666666666666",
  name: "Ridge replanting",
  type: "reforestation",
  category: "land",
  status: "published",
  description: "Two hundred acres of mixed conifer.",
  geometry: POLYGON,
  submittedByUserId: "22222222-2222-4222-8222-222222222222",
  submittedByTeamId: null,
  createdAt: new Date("2026-09-01T00:00:00.000Z"),
  updatedAt: new Date("2026-09-02T00:00:00.000Z"),
  reviewNote: null,
  hasFullGeometry: true,
};

function renderCard(overrides: Partial<InterventionDetailCardProps> = {}) {
  const props: InterventionDetailCardProps = {
    record: RECORD,
    isLoading: false,
    isError: false,
    isExpanded: false,
    onToggleExpanded: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<InterventionDetailCard {...props} />) };
}

afterEach(cleanup);

describe("InterventionDetailCard", () => {
  it("renders every field of the resolved record", () => {
    renderCard();

    expect(screen.getByText("Ridge replanting")).toBeTruthy();
    expect(screen.getByText("reforestation")).toBeTruthy();
    expect(screen.getByText("Land intervention")).toBeTruthy();
    expect(screen.getByText("Two hundred acres of mixed conifer.")).toBeTruthy();
    expect(
      screen.getByText("22222222-2222-4222-8222-222222222222")
    ).toBeTruthy();
    expect(screen.getByText("2026-09-01")).toBeTruthy();
    expect(screen.getByText("2026-09-02")).toBeTruthy();
  });

  it("draws the real polygon, every submitted position, not a centroid", () => {
    const { container } = renderCard();

    const svg = screen.getByTestId("intervention-geometry-preview");
    expect(svg.getAttribute("data-position-count")).toBe("5");
    // A closed outline, not a single pin: the polygon element is the shape.
    expect(container.querySelector("polygon")).toBeTruthy();
    expect(container.querySelectorAll("circle")).toHaveLength(5);
  });

  it("draws a point submission as its point", () => {
    const { container } = renderCard({
      record: {
        ...RECORD,
        geometry: { type: "Point", coordinates: [-120, 46] },
      },
    });

    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelector("polygon")).toBeNull();
  });

  it("says so rather than pretending a centroid is the drawn shape", () => {
    renderCard({
      record: {
        ...RECORD,
        geometry: { type: "Point", coordinates: [-120, 46] },
        hasFullGeometry: false,
      },
    });

    expect(screen.getByText(/Approximate location only/)).toBeTruthy();
  });

  it("paints an in-review status orange, whatever the category", () => {
    renderCard({ record: { ...RECORD, status: "pending_review", category: "air" } });

    const pill = screen.getByTestId("intervention-status-pill");
    expect(pill.getAttribute("data-status")).toBe("pending_review");
    expect(pill.textContent).toBe("In review");
    expect(pill.style.backgroundColor).toBe("rgb(249, 115, 22)");
    expect(INTERVENTION_PENDING_REVIEW_COLOR).toBe("#f97316");
  });

  it("shows the reviewer note only on a rejected row", () => {
    const rejected = {
      ...RECORD,
      status: "rejected",
      reviewNote: "Outside the eligible watershed.",
    };
    const { unmount } = renderCard({ record: rejected });
    expect(screen.getByTestId("intervention-review-note").textContent).toContain(
      "Outside the eligible watershed."
    );
    unmount();

    // The same note present on a published row is `castModerationVote`'s dead
    // vocabulary, not a reviewer decision the map honours: it stays hidden.
    renderCard({ record: { ...rejected, status: "published" } });
    expect(screen.queryByTestId("intervention-review-note")).toBeNull();
  });

  it("opens compact and expands to take over the viewport, Facebook-lightbox style", () => {
    const onToggleExpanded = vi.fn();
    const { unmount } = renderCard({ onToggleExpanded });

    const compact = screen.getByTestId("intervention-detail-modal");
    expect(compact.getAttribute("data-expanded")).toBe("false");
    // Bottom-centre, so the right-edge AI workspace anchor is never covered
    // while both are open.
    expect(compact.className).toContain("bottom-6");
    expect(compact.className).not.toContain("inset-4");

    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    expect(onToggleExpanded).toHaveBeenCalledTimes(1);
    unmount();

    renderCard({ isExpanded: true });
    const expanded = screen.getByTestId("intervention-detail-modal");
    expect(expanded.getAttribute("data-expanded")).toBe("true");
    expect(expanded.className).toContain("inset-4");
    expect(screen.getByRole("button", { name: "Collapse" })).toBeTruthy();
  });

  it("closes", () => {
    const onClose = vi.fn();
    renderCard({ onClose });

    fireEvent.click(screen.getByLabelText("Close intervention details"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows a loading state while the by-id fetch is in flight, and an error state after it fails", () => {
    const { unmount } = renderCard({ record: null, isLoading: true });
    expect(screen.getByText(/Loading intervention details/)).toBeTruthy();
    unmount();

    renderCard({ record: null, isError: true });
    expect(screen.getByRole("alert").textContent).toContain("could not be loaded");
  });

  it("renders the Phase 5 social slot without owning any social state", () => {
    renderCard({ children: <div data-testid="phase-5-slot" /> });
    expect(screen.getByTestId("phase-5-slot")).toBeTruthy();
  });
});

describe("InterventionDetailModal social mount (FR-4)", () => {
  afterEach(() => {
    useInterventionDetailStore.getState().close();
  });

  it("mounts the like control and the comment thread for the held record's id", () => {
    useInterventionDetailStore.getState().openWithRecord(RECORD);
    render(<InterventionDetailModal />);

    expect(
      screen.getByTestId("like-button-stub").getAttribute("data-feature-id")
    ).toBe(RECORD.id);
    expect(
      screen.getByTestId("comment-thread-stub").getAttribute("data-feature-id")
    ).toBe(RECORD.id);
  });

  it("mounts them for a by-id open too, while the record is still fetching", () => {
    useInterventionDetailStore.getState().openById(RECORD.id);
    render(<InterventionDetailModal />);

    expect(
      screen.getByTestId("like-button-stub").getAttribute("data-feature-id")
    ).toBe(RECORD.id);
    expect(screen.getByTestId("comment-thread-stub")).toBeTruthy();
  });

  it("mounts nothing at all while the store is closed", () => {
    render(<InterventionDetailModal />);
    expect(screen.queryByTestId("like-button-stub")).toBeNull();
  });
});
