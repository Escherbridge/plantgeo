import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * What the two location actions actually open, after Phase 3 replaced MapView's two independent
 * overlay mounts with one workspace.
 *
 * Deliberately a separate file from `map-view-render-count.test.tsx`: that file stubs
 * `AgentInteraction` to null because its subject is MapView's subscription set, so its buttons
 * cannot be clicked there. This one renders the real popup and the real shell, and asserts the
 * routing between them -- which mode opens, with which coordinate, and that a second click does
 * not destroy the first one's work.
 */

const stub = () => null;

const fakeMap = vi.hoisted(() => ({
  fire: null as null | ((type: string, event: unknown) => void),
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    private readonly handlers = new Map<string, Set<(...args: unknown[]) => void>>();
    private readonly canvas = document.createElement("canvas");
    private readonly container = document.createElement("div");

    constructor() {
      // Only the FIRST map built in a test is the main map. Since Phase 4 the workspace shell
      // builds a second, embedded drawing map; letting it claim `fire` would point every
      // simulated click at a canvas with no handlers on it.
      fakeMap.fire ??= (type: string, event: unknown) => {
        for (const handler of this.handlers.get(type) ?? []) handler(event);
      };
    }
    // MapLibre's real `on`/`off` overload on a third, layer-id argument for a layer-scoped
    // listener (`map.on("mousemove", layerId, handler)`, as `LandContextLayer` uses for its
    // hover wiring; its click is a bare map listener) -- this fake never filters by layer, so
    // it just needs to find the actual handler regardless of which position it landed in.
    on(type: string, layerIdOrHandler: string | ((...args: unknown[]) => void), maybeHandler?: (...args: unknown[]) => void) {
      const handler = maybeHandler ?? (layerIdOrHandler as (...args: unknown[]) => void);
      const set = this.handlers.get(type) ?? new Set();
      set.add(handler);
      this.handlers.set(type, set);
      return this;
    }
    once(type: string, handler: (...args: unknown[]) => void) {
      return this.on(type, handler);
    }
    off(type: string, layerIdOrHandler: string | ((...args: unknown[]) => void), maybeHandler?: (...args: unknown[]) => void) {
      const handler = maybeHandler ?? (layerIdOrHandler as (...args: unknown[]) => void);
      this.handlers.get(type)?.delete(handler);
      return this;
    }
    addControl() {
      return this;
    }
    getCanvas() {
      return this.canvas;
    }
    getContainer() {
      return this.container;
    }
    getCenter() {
      return { lng: -120, lat: 46 };
    }
    getZoom() {
      return 5;
    }
    getBearing() {
      return 0;
    }
    getPitch() {
      return 0;
    }
    getSource() {
      return undefined;
    }
    isStyleLoaded() {
      return false;
    }
    queryRenderedFeatures() {
      return [];
    }
    setStyle() {}
    setSky() {}
    setTerrain() {}
    setProjection() {}
    easeTo() {}
    remove() {}
    resize() {}
  }
  const control = class {};
  return {
    default: {
      Map: FakeMap,
      NavigationControl: control,
      ScaleControl: control,
      GeolocateControl: control,
      FullscreenControl: control,
      addProtocol: vi.fn(),
      removeProtocol: vi.fn(),
    },
  };
});

vi.mock("pmtiles", () => ({ Protocol: class { tile = vi.fn(); } }));
vi.mock("@/lib/map/styles", () => ({
  getStyle: () => ({ version: 8, sources: {}, layers: [] }),
  skyThemes: new Proxy({}, { get: () => ({}) }),
}));

// The analysis controller's network side, stubbed at its own boundary: this file is about what
// the click opens, not about what the request does.
const queryLocation = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useRegionalIntelligence", () => ({
  useRegionalIntelligence: () => ({ queryLocation }),
}));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    useUtils: () => ({}),
    interventions: {
      submitIntervention: { useMutation: () => ({ mutate: vi.fn(), isPending: false }) },
    },
    // `LandContextController` mounts unconditionally in `MapView` and queries both routes on
    // every render; this file is about workspace routing, not land-context, so both queries stay
    // permanently idle rather than asserting anything about their data.
    landContext: {
      resolveBoundaryAtPoint: {
        useQuery: () => ({ data: undefined, isLoading: false, isError: false }),
      },
      resolveBoundaryInArea: {
        useQuery: () => ({ data: undefined, isLoading: false, isError: false }),
      },
      lookupContactsForSelection: {
        useQuery: () => ({ data: undefined, isLoading: false, isError: false }),
      },
    },
  },
}));
vi.mock("@/components/map/InterventionDrawControl", () => ({
  InterventionDrawControl: () => null,
}));
vi.mock("@/lib/map/use-intervention-drafts", () => ({
  invalidateInterventionDraftsOverlay: vi.fn(),
}));
vi.mock("@/components/panels/RegionalIntelligencePanel", () => ({ default: stub }));

vi.mock("@/components/map/DataLoadingChip", () => ({ DataLoadingChip: stub }));
vi.mock("@/components/map/MapFocus", () => ({ MapFocus: stub }));
vi.mock("@/components/map/MapKeyboardShortcuts", () => ({ default: stub }));
vi.mock("@/components/map/LayerManager", () => ({ default: stub }));
vi.mock("@/components/map/HoverTooltip", () => ({ default: stub }));
vi.mock("@/components/map/MapDateSummary", () => ({ MapDateSummary: stub }));
vi.mock("@/components/map/TimeSliderCapabilitiesLoader", () => ({ default: stub }));
vi.mock("@/components/map/ServiceAreaLayer", () => ({ ServiceAreaLayer: stub }));
vi.mock("@/components/map/layer-panel/ManagerRail", () => ({ ManagerRail: stub }));
vi.mock("@/components/map/layer-panel/LayerPanel", () => ({ LayerPanel: stub }));
vi.mock("@/components/search/ReverseGeocode", () => ({ ReverseGeocode: stub }));
vi.mock("@/components/ui/SyncIndicator", () => ({ SyncIndicator: stub }));

const { default: MapView } = await import("@/components/map/MapView");
const { useMapStore, DEFAULT_VIEWPORT } = await import("@/stores/map-store");
const { useInterventionDraftStore } = await import("@/stores/intervention-draft-store");
const { useRegionalIntelligenceStore } = await import(
  "@/stores/regional-intelligence-store"
);

/** One map click, which is what opens the confirm-before-analyse popup. */
function clickMapAt(lng: number, lat: number) {
  act(() => {
    fakeMap.fire!("click", { point: { x: 10, y: 10 }, lngLat: { lng, lat } });
  });
}

beforeEach(() => {
  fakeMap.fire = null;
  useMapStore.setState({
    viewport: { ...DEFAULT_VIEWPORT },
    activeLayers: [],
    isCapturingQueryPoint: false,
    is3DEnabled: false,
    isGlobeView: false,
    isTerrainEnabled: false,
    currentStyle: "satellite",
  });
  useRegionalIntelligenceStore.setState({ isOpen: false, isLoading: false, messages: [] });
  useInterventionDraftStore.getState().clearDraft();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MapView workspace routing", () => {
  it('opens the workspace in AI mode from "Send for analysis", at the clicked point', async () => {
    render(<MapView />);
    clickMapAt(-120.123456, 46.654321);

    fireEvent.click(screen.getByRole("button", { name: /send for analysis/i }));

    const workspace = await screen.findByTestId("ai-intervention-workspace");
    expect(workspace).toBeTruthy();
    expect(
      screen.getByRole("tab", { name: /ai analysis/i }).getAttribute("aria-selected")
    ).toBe("true");
    // Approximate is the default precision, so the confirmed point is the rounded one -- the
    // same one handed to the analysis request.
    expect(workspace.textContent).toContain("46.65, -120.12");
    expect(queryLocation).toHaveBeenCalledWith(46.65, -120.12, undefined, "approximate");
  });

  it('opens the workspace in intervention mode from "Propose intervention here", at the clicked point', async () => {
    render(<MapView />);
    clickMapAt(-118.5, 44.25);

    fireEvent.click(screen.getByRole("button", { name: /propose intervention here/i }));

    await screen.findByTestId("ai-intervention-workspace");
    expect(
      screen
        .getByRole("tab", { name: /propose intervention/i })
        .getAttribute("aria-selected")
    ).toBe("true");
    await waitFor(() => {
      expect(useInterventionDraftStore.getState().lat).toBe(44.25);
    });
    expect(useInterventionDraftStore.getState().lon).toBe(-118.5);
  });

  it("does not silently clear an unsubmitted draft when a new map click opens the workspace again", async () => {
    render(<MapView />);
    clickMapAt(-118.5, 44.25);
    fireEvent.click(screen.getByRole("button", { name: /propose intervention here/i }));
    await screen.findByTestId("ai-intervention-workspace");

    act(() => {
      useInterventionDraftStore.getState().setGeometry({
        type: "Point",
        coordinates: [-118.5, 44.25],
      });
      useInterventionDraftStore.getState().setName("Creekside planting");
    });

    // A second, different click, then the other action entirely.
    clickMapAt(-121, 47);
    fireEvent.click(screen.getByRole("button", { name: /send for analysis/i }));
    await screen.findByTestId("ai-intervention-workspace");

    const draft = useInterventionDraftStore.getState();
    expect(draft.geometry).toEqual({ type: "Point", coordinates: [-118.5, 44.25] });
    expect(draft.name).toBe("Creekside planting");
    expect(draft.lat).toBe(44.25);
    // And the user is told the older draft is still there rather than being left to guess.
    expect(screen.getByTestId("workspace-draft-relocate")).toBeTruthy();
  });
});
