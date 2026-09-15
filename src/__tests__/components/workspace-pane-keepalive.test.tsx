import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * Phase 4's whole claim, tested: porting the two real panes into the workspace shell did not
 * cost either of them its life across a mode switch (OQ-1(a), true keep-alive).
 *
 * Two things can be destroyed by an unmount and cannot be rebuilt from the stores: an in-flight
 * analysis stream (its `AbortController` dies with the component that owns the request) and the
 * embedded MapLibre instance with terra-draw's live session on it. Both are asserted here by
 * switching modes away and back mid-work.
 */

/** jsdom implements no scroll behaviour; the analysis pane calls this on every message change. */
Element.prototype.scrollIntoView = vi.fn();

const mapSpies = vi.hoisted(() => ({
  constructed: [] as unknown[],
  removed: 0,
  resized: 0,
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor(public readonly options: unknown) {
      mapSpies.constructed.push(this);
    }
    remove() {
      mapSpies.removed += 1;
    }
    resize() {
      mapSpies.resized += 1;
    }
  }
  return { default: { Map: FakeMap } };
});

vi.mock("@/lib/map/styles", () => ({
  getStyle: () => ({ version: 8, sources: {}, layers: [] }),
  skyThemes: new Proxy({}, { get: () => ({}) }),
}));

const submitStub = vi.hoisted(() => ({
  mutate: vi.fn(),
  options: null as { onSuccess?: () => void; onError?: (error: { message: string }) => void } | null,
}));
const analysisSpy = vi.hoisted(() => ({ queryLocation: vi.fn() }));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({}),
    interventions: {
      submitIntervention: {
        useMutation: (options: typeof submitStub.options) => {
          submitStub.options = options;
          return { mutate: submitStub.mutate, isPending: false };
        },
      },
    },
    regionalIntelligence: {
      getConversation: { useQuery: () => ({ data: undefined }) },
      setMessageFeedback: { useMutation: () => ({ mutate: vi.fn(), isPending: false }) },
    },
  },
}));

/** The analysis controller's network side. The store is the real one; only the fetch is stubbed. */
vi.mock("@/hooks/useRegionalIntelligence", () => ({
  useRegionalIntelligence: () => ({
    queryLocation: analysisSpy.queryLocation,
    sendFollowUp: vi.fn(),
    retryLastRequest: vi.fn(),
  }),
}));

/** terra-draw needs a real WebGL map; the draw session's survival is proved by the map's. */
vi.mock("@/components/map/InterventionDrawControl", () => ({
  InterventionDrawControl: () => <div data-testid="draw-control-stub" />,
}));

import { AiInterventionWorkspace } from "@/components/map/AiInterventionWorkspace";
import RegionalIntelligencePanel from "@/components/panels/RegionalIntelligencePanel";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";
import { useMapStore } from "@/stores/map-store";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

const DRAWN_POLYGON: InterventionGeometry = {
  type: "Polygon",
  coordinates: [
    [
      [-116.3, 43.6],
      [-116.2, 43.6],
      [-116.2, 43.7],
      [-116.3, 43.6],
    ],
  ],
};

function switchTo(mode: "ai" | "intervention") {
  fireEvent.click(
    screen.getByRole("tab", {
      name: mode === "ai" ? /ai analysis/i : /propose intervention/i,
    })
  );
}

beforeEach(() => {
  submitStub.mutate.mockClear();
  submitStub.options = null;
  mapSpies.constructed.length = 0;
  mapSpies.removed = 0;
  mapSpies.resized = 0;
  useInterventionDraftStore.getState().clearDraft();
  useRegionalIntelligenceStore.getState().closePanel();
  useRegionalIntelligenceStore.setState({ isVisible: true });
  useMapStore.setState({ activeLayers: [], currentStyle: "satellite" });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("workspace AI pane keep-alive", () => {
  it("defaults back to approximate consent when an empty proposal moves to a new location", () => {
    const onClose = vi.fn();
    const { rerender } = render(<AiInterventionWorkspace coordinates={[-116.23456789, 43.61234567]} initialMode="ai" onClose={onClose} />);
    fireEvent.click(screen.getByRole("radio", { name: /high-precision selected location/i }));
    rerender(<AiInterventionWorkspace coordinates={[-115.3456789, 42.7654321]} initialMode="ai" onClose={onClose} />);
    expect((screen.getByRole("radio", { name: /approximate location/i }) as HTMLInputElement).checked).toBe(true);
    expect(analysisSpy.queryLocation).not.toHaveBeenCalled();
  });

  it("does not send analysis when the proposal opens, tabs switch, precision changes, or consent is canceled", () => {
    render(<AiInterventionWorkspace coordinates={[-116.23456789, 43.61234567]} initialMode="intervention" onClose={vi.fn()} />);
    expect(analysisSpy.queryLocation).not.toHaveBeenCalled();
    switchTo("ai");
    expect((screen.getByRole("radio", { name: /approximate location/i }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /high-precision selected location/i }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByTestId("intervention-mode-content").hasAttribute("hidden")).toBe(false);
    expect(useRegionalIntelligenceStore.getState().isOpen).toBe(false);
    expect(analysisSpy.queryLocation).not.toHaveBeenCalled();
  });

  it.each([
    { precision: "approximate", lat: 43.61, lon: -116.23 },
    { precision: "exact", lat: 43.612346, lon: -116.234568 },
  ] as const)("starts $precision analysis of the preserved proposal location without losing the drawing session", ({ precision, lat, lon }) => {
    const onClose = vi.fn();
    const { rerender } = render(
      <AiInterventionWorkspace coordinates={[-116.23456789, 43.61234567]} initialMode="intervention" onClose={onClose} />
    );
    const instance = mapSpies.constructed[0];
    const drawContainer = screen.getByTestId("intervention-draw-map");
    act(() => {
      useInterventionDraftStore.getState().setName("Keep this proposal");
      useInterventionDraftStore.getState().setGeometry(DRAWN_POLYGON);
    });
    rerender(<AiInterventionWorkspace coordinates={[-100, 40]} initialMode="intervention" onClose={onClose} />);
    switchTo("ai");
    if (precision === "exact") {
      fireEvent.click(screen.getByRole("radio", { name: /high-precision selected location/i }));
    }
    expect(analysisSpy.queryLocation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send for analysis" }));
    expect(analysisSpy.queryLocation).toHaveBeenCalledExactlyOnceWith(lat, lon, undefined, precision);
    expect(useRegionalIntelligenceStore.getState()).toMatchObject({
      isOpen: true, isVisible: false, selectedLocation: { lat, lon, precision },
    });
    expect(screen.getByTestId("ai-intervention-workspace").textContent).toContain(`${lat}, ${lon}`);
    switchTo("intervention");
    expect(useInterventionDraftStore.getState()).toMatchObject({
      name: "Keep this proposal", geometry: DRAWN_POLYGON, lat: 43.61234567, lon: -116.23456789,
    });
    expect(screen.getByTestId("intervention-draw-map")).toBe(drawContainer);
    expect(mapSpies.constructed).toEqual([instance]);
    expect(mapSpies.removed).toBe(0);
    switchTo("ai");
    expect(analysisSpy.queryLocation).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps the hidden standalone panel from bypassing a canceled workspace Escape", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const controller = new AbortController();
    const onClose = vi.fn();
    useRegionalIntelligenceStore.getState().openPanel(43.6, -116.2, "approximate");
    useRegionalIntelligenceStore.getState().setLoading(true);
    useRegionalIntelligenceStore.getState().setAbortController(controller);
    const { rerender } = render(<RegionalIntelligencePanel />);
    rerender(
      <>
        <RegionalIntelligencePanel />
        <AiInterventionWorkspace coordinates={[-116.2, 43.6]} initialMode="ai" onClose={onClose} />
      </>
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(useRegionalIntelligenceStore.getState().isOpen).toBe(true);
    expect(useRegionalIntelligenceStore.getState().isLoading).toBe(true);
    expect(controller.signal.aborted).toBe(false);
    confirmSpy.mockReturnValue(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(controller.signal.aborted).toBe(true);
  });
  it("keeps an in-flight analysis streaming while the mode is switched away and back", async () => {
    const abortController = new AbortController();
    act(() => {
      useRegionalIntelligenceStore.getState().openPanel(43.6, -116.2, "approximate");
      useRegionalIntelligenceStore.getState().addMessage({
        id: "assistant-1",
        role: "assistant",
        content: "Reviewing",
        isStreaming: true,
      });
      useRegionalIntelligenceStore.getState().setLoading(true);
      useRegionalIntelligenceStore.getState().setAbortController(abortController);
    });

    render(
      <AiInterventionWorkspace
        coordinates={[-116.2, 43.6]}
        initialMode="ai"
        onClose={vi.fn()}
      />
    );

    const panelBefore = await screen.findByTestId("regional-intelligence-panel");
    expect(panelBefore.textContent).toContain("Reviewing");

    switchTo("intervention");

    // A token arrives while the AI pane is the HIDDEN one. If switching modes had unmounted it,
    // this write would be landing in a store nothing is rendering -- and the request behind it
    // would already have been aborted by the unmount.
    act(() => {
      useRegionalIntelligenceStore
        .getState()
        .updateLastMessage({ content: "Reviewing this location, drought is elevated" });
      useRegionalIntelligenceStore.getState().setToolActivity("Reading drought");
    });

    expect(abortController.signal.aborted).toBe(false);
    expect(useRegionalIntelligenceStore.getState().isLoading).toBe(true);
    // Still the very same DOM node, merely inside a hidden slot.
    expect(screen.getByTestId("regional-intelligence-panel")).toBe(panelBefore);
    expect(panelBefore.textContent).toContain("drought is elevated");

    switchTo("ai");

    expect(screen.getByTestId("regional-intelligence-panel")).toBe(panelBefore);
    expect(screen.getByTestId("ai-mode-content").hasAttribute("hidden")).toBe(false);
    expect(panelBefore.textContent).toContain("drought is elevated");
    expect(useRegionalIntelligenceStore.getState().isLoading).toBe(true);
  });

  it("stands the standalone overlay copy down while it embeds the conversation, and restores it on unmount", () => {
    act(() => {
      useRegionalIntelligenceStore.getState().openPanel(43.6, -116.2, "approximate");
    });

    const { unmount } = render(
      <AiInterventionWorkspace
        coordinates={[-116.2, 43.6]}
        initialMode="ai"
        onClose={vi.fn()}
      />
    );
    expect(useRegionalIntelligenceStore.getState().isVisible).toBe(false);
    // Visibility only: the conversation itself is untouched.
    expect(useRegionalIntelligenceStore.getState().isOpen).toBe(true);

    unmount();
    expect(useRegionalIntelligenceStore.getState().isVisible).toBe(true);
  });
});

describe("workspace intervention pane keep-alive", () => {
  it("resizes a hidden-created draw map on each reveal without recreating it", () => {
    render(<AiInterventionWorkspace coordinates={[-116.2, 43.6]} initialMode="ai" onClose={vi.fn()} />);
    expect(mapSpies.resized).toBe(0);
    switchTo("intervention");
    expect(mapSpies.resized).toBe(1);
    switchTo("ai");
    expect(mapSpies.resized).toBe(1);
    switchTo("intervention");
    expect(mapSpies.resized).toBe(2);
    expect(mapSpies.constructed).toHaveLength(1);
    expect(mapSpies.removed).toBe(0);
  });
  it("keeps one MapLibre instance, and the drawn geometry, across a switch away and back", async () => {
    render(
      <AiInterventionWorkspace
        coordinates={[-116.2, 43.6]}
        initialMode="intervention"
        onClose={vi.fn()}
      />
    );

    expect(mapSpies.constructed).toHaveLength(1);
    const instance = mapSpies.constructed[0];
    const container = screen.getByTestId("intervention-draw-map");
    expect(await screen.findByTestId("draw-control-stub")).toBeTruthy();

    act(() => {
      useInterventionDraftStore.getState().setGeometry(DRAWN_POLYGON);
    });
    fireEvent.change(screen.getByLabelText(/Site Name/), {
      target: { value: "Ridge silvopasture plot" },
    });

    switchTo("ai");
    switchTo("intervention");

    // Same instance, never removed and never rebuilt: terra-draw is attached to THIS object, so
    // a new one would silently drop the live drawing session even though the geometry value
    // survives in the store.
    expect(mapSpies.constructed).toHaveLength(1);
    expect(mapSpies.constructed[0]).toBe(instance);
    expect(mapSpies.removed).toBe(0);
    expect(screen.getByTestId("intervention-draw-map")).toBe(container);
    expect(screen.getByTestId("draw-control-stub")).toBeTruthy();

    expect(useInterventionDraftStore.getState().geometry).toEqual(DRAWN_POLYGON);
    expect((screen.getByLabelText(/Site Name/) as HTMLInputElement).value).toBe(
      "Ridge silvopasture plot"
    );
  });

  it("submits the draft the store holds, then clears it and tells the map to refresh", () => {
    const onInterventionSubmitted = vi.fn();
    render(
      <AiInterventionWorkspace
        coordinates={[-116.2, 43.6]}
        initialMode="intervention"
        onClose={vi.fn()}
        onInterventionSubmitted={onInterventionSubmitted}
      />
    );

    fireEvent.change(screen.getByLabelText(/Site Name/), {
      target: { value: "Ridge silvopasture plot" },
    });
    fireEvent.click(screen.getByLabelText(/I understand this is a recommendation/));
    act(() => {
      useInterventionDraftStore.getState().setGeometry(DRAWN_POLYGON);
    });

    fireEvent.click(screen.getByRole("button", { name: /Submit Recommendation/ }));

    expect(submitStub.mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Ridge silvopasture plot",
        geometry: DRAWN_POLYGON,
        category: "land",
        publicationConsent: true,
      })
    );

    // The server now owns the proposal, so this is the ONE place the draft clears itself.
    act(() => submitStub.options?.onSuccess?.());

    expect(useInterventionDraftStore.getState().geometry).toBeNull();
    expect(useInterventionDraftStore.getState().name).toBe("");
    expect(onInterventionSubmitted).toHaveBeenCalledTimes(1);
  });
});
