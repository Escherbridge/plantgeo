import { Profiler, type ReactNode } from "react";
import { act, render, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * MapView owns the MapLibre instance and mounts every layer component under it, so one of its
 * renders is the most expensive render on the page. It used to subscribe to two whole Zustand
 * stores, which meant a feature selection, a layer toggle or a single streaming token of an AI
 * analysis re-rendered the entire map subtree. This file pins the narrow-selector contract:
 * a write to a field MapView does not read must not reach it, and a write to a field it does
 * read must.
 *
 * Child COMPONENTS are stubbed -- the subject is MapView's own subscription set, not what it
 * composes, and a real child would make the commit count say something about the child instead.
 * Its HOOKS are not. `useRegionalIntelligence` was stubbed here until 2026-09-03 with a
 * subscription-free fake, which is the one thing a subscription test may not do: through
 * `useViewedLayerDays` -> `useLayerVisibility` -> `useActiveLayerToggles` the real hook
 * subscribes its caller to `activeLayers`, so the "a layer toggle costs nothing" assertion was
 * measuring the stub. MapView no longer holds that hook -- `AgentAnalysisPrompt` does, and it
 * mounts only while a location is selected -- and the assertion below now runs against the real
 * chain, so re-hoisting the call into MapView fails this file instead of passing it.
 *
 * The one stub at a service boundary is `src/test/setup.ts`'s global `fetch`; nothing here
 * reaches it, because no test sends an analysis.
 */

const stub = () => null;

/**
 * A way to fire a MapLibre event at the map MapView actually built. Only the "click" handler
 * is used, to reach the one branch that mounts a child holding a store subscription of its own.
 */
const fakeMap = vi.hoisted(() => ({
  fire: null as null | ((type: string, event: unknown) => void),
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    private readonly handlers = new Map<string, Set<(...args: unknown[]) => void>>();
    private readonly canvas = document.createElement("canvas");
    private readonly container = document.createElement("div");

    constructor() {
      fakeMap.fire = (type: string, event: unknown) => {
        for (const handler of this.handlers.get(type) ?? []) handler(event);
      };
    }

    on(type: string, handler: (...args: unknown[]) => void) {
      const set = this.handlers.get(type) ?? new Set();
      set.add(handler);
      this.handlers.set(type, set);
      return this;
    }
    once(type: string, handler: (...args: unknown[]) => void) {
      return this.on(type, handler);
    }
    off(type: string, handler: (...args: unknown[]) => void) {
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
    // False throughout: the render-mode effects then take their early return instead of
    // driving a fake camera, which keeps this file about subscriptions.
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

vi.mock("pmtiles", () => ({
  Protocol: class {
    tile = vi.fn();
  },
}));

vi.mock("@/lib/map/styles", () => ({
  getStyle: () => ({ version: 8, sources: {}, layers: [] }),
  skyThemes: new Proxy({}, { get: () => ({}) }),
}));

vi.mock("@/components/map/DataLoadingChip", () => ({ DataLoadingChip: stub }));
vi.mock("@/components/map/MapFocus", () => ({ MapFocus: stub }));
vi.mock("@/components/map/MapKeyboardShortcuts", () => ({ default: stub }));
vi.mock("@/components/map/LayerManager", () => ({ default: stub }));
vi.mock("@/components/map/HoverTooltip", () => ({ default: stub }));
vi.mock("@/components/map/MapDateSummary", () => ({ MapDateSummary: stub }));
vi.mock("@/components/map/TimeSliderCapabilitiesLoader", () => ({ default: stub }));
vi.mock("@/components/map/ServiceAreaLayer", () => ({ ServiceAreaLayer: stub }));
vi.mock("@/components/map/AgentInteraction", () => ({ AgentInteraction: stub }));
vi.mock("@/components/map/layer-panel/ManagerRail", () => ({ ManagerRail: stub }));
vi.mock("@/components/map/layer-panel/LayerPanel", () => ({ LayerPanel: stub }));
vi.mock("@/components/search/ReverseGeocode", () => ({ ReverseGeocode: stub }));
vi.mock("@/components/ui/SyncIndicator", () => ({ SyncIndicator: stub }));

const { default: MapView } = await import("@/components/map/MapView");
const { useMapStore, DEFAULT_VIEWPORT } = await import("@/stores/map-store");
const { useRegionalIntelligenceStore } = await import(
  "@/stores/regional-intelligence-store"
);

let commits = 0;

function Subject({ children }: { children: ReactNode }) {
  return (
    <Profiler id="map-view" onRender={() => { commits += 1; }}>
      {children}
    </Profiler>
  );
}

/** Mounts MapView, lets its init effects settle, and zeroes the commit counter. */
function mountSettled() {
  render(
    <Subject>
      <MapView />
    </Subject>
  );
  commits = 0;
}

beforeEach(() => {
  commits = 0;
  useMapStore.setState({
    viewport: { ...DEFAULT_VIEWPORT },
    activeLayers: [],
    selectedFeatureId: null,
    queryPoint: null,
    isCapturingQueryPoint: false,
    is3DEnabled: false,
    isGlobeView: false,
    isTerrainEnabled: false,
    terrainExaggeration: 1.5,
    currentStyle: "satellite",
  });
  useRegionalIntelligenceStore.setState({
    isOpen: false,
    messages: [],
    toolActivity: null,
    isLoading: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MapView store subscriptions", () => {
  it("does not re-render when a map-store field it never reads changes", () => {
    mountSettled();

    act(() => {
      useMapStore.getState().selectFeature("feature-1");
      useMapStore.getState().setCapturingQueryPoint(true);
    });

    expect(useMapStore.getState().selectedFeatureId).toBe("feature-1");
    expect(commits).toBe(0);
  });

  /**
   * `activeLayers` is not in MapView's selector list, but it reached MapView anyway until
   * 2026-09-03 through the real `useRegionalIntelligence` -> `useViewedLayerDays` ->
   * `useLayerVisibility` -> `useActiveLayerToggles` chain. Nothing here stubs that chain, so
   * this passes only while the analysis controller lives below MapView rather than in it.
   */
  it("does not re-render when a layer is toggled", () => {
    mountSettled();

    act(() => {
      useMapStore.getState().toggleLayer("fire");
    });

    expect(useMapStore.getState().activeLayers).toEqual(["fire"]);
    expect(commits).toBe(0);
  });

  it("does not re-render while an AI analysis streams", () => {
    mountSettled();

    // The store this exercises is written once per streaming delta. Reading it whole is what
    // made MapView re-render per token; only `isOpen` is MapView's business.
    act(() => {
      const store = useRegionalIntelligenceStore.getState();
      store.addMessage({ id: "m1", role: "assistant", content: "", isStreaming: true });
      store.setToolActivity("reading soil");
      store.updateLastMessage({ content: "The" });
      store.updateLastMessage({ content: "The soil" });
      store.setLoading(true);
    });

    expect(useRegionalIntelligenceStore.getState().messages).toHaveLength(1);
    expect(commits).toBe(0);
  });

  /**
   * What a layer toggle DOES still cost, stated rather than hidden: the analysis controller
   * subscribes to `activeLayers` because it reports the visible layers' days with a request,
   * so while the confirm-before-analyse popup is open a toggle re-renders it (and
   * `RegionalIntelligencePanel` too, while that is also open). The point of the move is that
   * this is now the popup's lifetime and not the map's -- the case above proves the closed map
   * pays nothing. This Profiler wraps the whole subtree, so a commit here cannot by itself
   * distinguish the prompt re-rendering from MapView re-rendering; it only proves that opening
   * the prompt, and then toggling a layer while it is open, each commit at least once.
   */
  it("commits when the prompt opens, and again when a layer is toggled while it is open", () => {
    mountSettled();

    act(() => {
      fakeMap.fire!("click", {
        point: { x: 10, y: 10 },
        lngLat: { lng: -120, lat: 46 },
      });
    });
    expect(commits).toBeGreaterThan(0);

    commits = 0;
    act(() => {
      useMapStore.getState().toggleLayer("fire");
    });
    expect(commits).toBeGreaterThan(0);
  });

  // Without this every zero-commit assertion above would also pass on a MapView that never
  // re-renders at all, or on a Profiler wired to nothing.
  it("still re-renders when a field it does read changes", () => {
    mountSettled();

    act(() => {
      useMapStore.getState().toggleGlobe();
    });
    expect(commits).toBeGreaterThan(0);

    commits = 0;
    act(() => {
      useRegionalIntelligenceStore.getState().openPanel(46, -120, "approximate");
    });
    expect(commits).toBeGreaterThan(0);
  });
});
