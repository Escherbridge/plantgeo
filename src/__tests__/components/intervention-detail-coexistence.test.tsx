import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The detail modal and `AiInterventionWorkspace` coexist (OQ-3, FR-2's last
 * acceptance criterion).
 *
 * Two independent failure modes are pinned here. The first is STATE: opening
 * the modal must not close the workspace, switch its mode, or discard the
 * geometry and name a user has half-drawn in it -- which is what would happen if
 * the detail surface had been folded into that shell as a third mode. The second
 * is SPACE: the compact card must not sit permanently on top of the workspace's
 * right-edge anchor. A temporary full-viewport expand covering it is fine and is
 * exactly the lightbox pattern the product owner asked for; a default overlap is
 * not.
 */

vi.mock("maplibre-gl", () => ({
  default: {
    Map: class {
      remove() {}
    },
  },
}));
vi.mock("@/lib/map/styles", () => ({
  getStyle: () => ({ version: 8, sources: {}, layers: [] }),
  skyThemes: new Proxy({}, { get: () => ({}) }),
}));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      submitIntervention: {
        useMutation: () => ({ mutate: vi.fn(), isPending: false }),
      },
      getInterventionDetail: {
        useQuery: () => ({ data: undefined, isLoading: true, isError: false }),
      },
    },
  },
}));
/**
 * Phase 5's social children read a session this test never stands up, and their
 * own behaviour is pinned in `InterventionLikeButton.test.tsx` /
 * `InterventionCommentThread.test.tsx`. Stubbing them keeps this file about
 * coexistence, which is what it is for.
 */
vi.mock("@/components/intervention/InterventionLikeButton", () => ({
  InterventionLikeButton: () => <div data-testid="like-button-stub" />,
}));
vi.mock("@/components/intervention/InterventionCommentThread", () => ({
  InterventionCommentThread: () => <div data-testid="comment-thread-stub" />,
}));
vi.mock("@/components/map/InterventionDrawControl", () => ({
  InterventionDrawControl: () => null,
}));
vi.mock("@/components/panels/RegionalIntelligencePanel", () => ({
  default: () => <div data-testid="regional-panel-stub" />,
}));

import { AiInterventionWorkspace } from "@/components/map/AiInterventionWorkspace";
import { InterventionDetailModal } from "@/components/map/InterventionDetailModal";
import { useInterventionDetailStore } from "@/stores/intervention-detail-store";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";
import { useMapStore } from "@/stores/map-store";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";

const drawnPolygon: InterventionGeometry = {
  type: "Polygon",
  coordinates: [
    [
      [-120, 46],
      [-120.1, 46],
      [-120.1, 46.1],
      [-120, 46],
    ],
  ],
};

const CLICKED_RECORD: InterventionDetailRecord = {
  id: "66666666-6666-4666-8666-666666666666",
  name: "Ridge replanting",
  type: "reforestation",
  category: "land",
  status: "published",
  description: "Two hundred acres of mixed conifer.",
  geometry: { type: "Point", coordinates: [-120, 46] },
  submittedByUserId: null,
  submittedByTeamId: null,
  createdAt: null,
  updatedAt: null,
  reviewNote: null,
  hasFullGeometry: true,
};

function resetStores() {
  useInterventionDraftStore.getState().clearDraft();
  useInterventionDetailStore.getState().close();
  useRegionalIntelligenceStore.setState({ isLoading: false, abortController: null });
  useMapStore.setState({ activeLayers: [] });
}

function renderBoth(initialMode: "ai" | "intervention") {
  const onClose = vi.fn();
  render(
    <>
      <AiInterventionWorkspace
        coordinates={[-120, 46]}
        initialMode={initialMode}
        onClose={onClose}
      />
      <InterventionDetailModal />
    </>
  );
  return { onClose };
}

/** A map click resolving a drafts-overlay feature, as the click hook writes it. */
function openDetailModal() {
  act(() => useInterventionDetailStore.getState().openWithRecord(CLICKED_RECORD));
}

beforeEach(resetStores);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetStores();
});

describe("detail modal and AI workspace coexist", () => {
  it.each(["ai", "intervention"] as const)(
    "leaves the workspace open and on its %s mode",
    (initialMode) => {
      const { onClose } = renderBoth(initialMode);
      const activeTab =
        initialMode === "ai" ? /ai analysis/i : /propose intervention/i;

      openDetailModal();

      expect(screen.getByTestId("intervention-detail-modal")).toBeTruthy();
      expect(screen.getByRole("tab", { name: activeTab }).getAttribute("aria-selected")).toBe(
        "true"
      );
      expect(onClose).not.toHaveBeenCalled();
    }
  );

  it("does not discard the workspace's unsubmitted draft", () => {
    renderBoth("intervention");
    act(() => {
      useInterventionDraftStore.getState().setGeometry(drawnPolygon);
      useInterventionDraftStore.getState().setName("Ridge planting");
    });

    openDetailModal();

    const draft = useInterventionDraftStore.getState();
    expect(draft.geometry).toEqual(drawnPolygon);
    expect(draft.name).toBe("Ridge planting");
  });

  it("does not interrupt an in-flight AI analysis", () => {
    renderBoth("ai");
    act(() => useRegionalIntelligenceStore.getState().setLoading(true));

    openDetailModal();

    expect(useRegionalIntelligenceStore.getState().isLoading).toBe(true);
  });

  it("closing the modal leaves the workspace untouched", () => {
    const { onClose } = renderBoth("ai");
    openDetailModal();

    act(() => useInterventionDetailStore.getState().close());

    expect(screen.queryByTestId("intervention-detail-modal")).toBeNull();
    expect(screen.getByRole("tab", { name: /ai analysis/i })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not anchor the compact card over the workspace's right edge", () => {
    renderBoth("ai");
    openDetailModal();

    const modal = screen.getByTestId("intervention-detail-modal");
    expect(modal.getAttribute("data-expanded")).toBe("false");
    // The workspace owns `right-0` at `w-[min(24rem,calc(100vw-19rem))]`; the
    // compact card is bottom-centred and claims neither edge.
    expect(modal.className).not.toContain("right-0");
    expect(modal.className).toContain("bottom-6");
  });

  it("covers the workspace only while expanded, and gives the space back on collapse", () => {
    renderBoth("ai");
    openDetailModal();

    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    const expanded = screen.getByTestId("intervention-detail-modal");
    expect(expanded.getAttribute("data-expanded")).toBe("true");
    expect(expanded.className).toContain("inset-4");
    // Still there underneath: an expand is a temporary overlay, not an unmount.
    expect(screen.getByRole("tab", { name: /ai analysis/i })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
    const collapsed = screen.getByTestId("intervention-detail-modal");
    expect(collapsed.getAttribute("data-expanded")).toBe("false");
    expect(collapsed.className).not.toContain("inset-4");
  });
});
