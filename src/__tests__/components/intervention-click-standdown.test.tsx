import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setScalarFieldInspectionSuppressed } from "@/lib/map/scalar-field-inspection";
import { INTERVENTION_STYLE_LAYER_IDS } from "@/lib/map/layer-registry";

/**
 * One click, one surface.
 *
 * `MapView`'s bare `map.on("click")` opens the AI-analysis coordinate popup for
 * a click on empty ground. A click that lands on an intervention feature is not
 * empty ground -- it belongs to the detail modal
 * (`use-intervention-detail-clicks.ts`) -- so this file pins the stand-down for
 * all six merged style layers, including while those layers are marked
 * inspection-suppressed, which is the state the `isScalarFieldInspectionAllowed`
 * arm alone would have handed the click back in.
 *
 * The mock scaffolding is deliberately the same as
 * `map-view-render-count.test.tsx`'s, with one difference: `AgentInteraction`
 * renders a marker instead of null, because its presence IS the assertion.
 */

const stub = () => null;

const fakeMap = vi.hoisted(() => ({
  fire: null as null | ((type: string, event: unknown) => void),
  instance: null as object | null,
  features: [] as { layer: { id: string } }[],
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    private readonly handlers = new Map<string, Set<(...args: unknown[]) => void>>();
    private readonly canvas = document.createElement("canvas");
    private readonly container = document.createElement("div");

    constructor() {
      fakeMap.instance = this;
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
    isStyleLoaded() {
      return false;
    }
    queryRenderedFeatures() {
      return fakeMap.features;
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
vi.mock("@/components/map/AgentInteraction", () => ({
  AgentInteraction: () => <div data-testid="agent-popup" />,
}));
vi.mock("@/components/map/layer-panel/ManagerRail", () => ({ ManagerRail: stub }));
vi.mock("@/components/map/layer-panel/LayerPanel", () => ({ LayerPanel: stub }));
vi.mock("@/components/search/ReverseGeocode", () => ({ ReverseGeocode: stub }));
vi.mock("@/components/ui/SyncIndicator", () => ({ SyncIndicator: stub }));
// This file is about the intervention click stand-down, not land-context; its components each
// query the real `landContext` tRPC router, which has no provider in this test tree.
vi.mock("@/components/map/land-context", () => ({
  LandContextController: stub,
  LandContextLayer: stub,
  LandContextIdentityCard: stub,
  LandContextAccessibleFeatureList: stub,
}));
vi.mock("@/components/panels/land-context", () => ({ LandContextPanelHost: stub }));

const { default: MapView } = await import("@/components/map/MapView");
const { useMapStore, DEFAULT_VIEWPORT } = await import("@/stores/map-store");
const { useLandContextStore } = await import("@/stores/land-context-store");

function clickMap() {
  act(() => {
    fakeMap.fire!("click", {
      point: { x: 10, y: 10 },
      lngLat: { lng: -120, lat: 46 },
    });
  });
}

beforeEach(() => {
  // Real ground: the basemap's unfiltered `earth` fill is under every land pixel in production
  // (styles.ts), so "empty ground" must be modelled as that feature, never as `[]`.
  fakeMap.features = [{ layer: { id: "earth" } }];
  useLandContextStore.setState({
    enabledGroups: {
      "parcels-land-use": false,
      "electric-utility-territories": false,
      "blm-lands": false,
      "state-managed-lands": false,
    },
  });
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
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MapView stands down over intervention features", () => {
  it("opens the AI popup on empty ground, so the negatives below mean something", () => {
    render(<MapView />);
    clickMap();
    expect(screen.getByTestId("agent-popup")).toBeTruthy();
  });

  it.each([...INTERVENTION_STYLE_LAYER_IDS])(
    "does not open the AI popup for a click on %s",
    (layerId) => {
      render(<MapView />);
      fakeMap.features = [{ layer: { id: "earth" } }, { layer: { id: layerId } }];
      clickMap();
      expect(screen.queryByTestId("agent-popup")).toBeNull();
    }
  );

  it("stands down while a land-context group is on; right-click still reaches the popup", () => {
    render(<MapView />);
    useLandContextStore.setState((state) => ({
      enabledGroups: { ...state.enabledGroups, "blm-lands": true },
    }));

    clickMap();
    expect(screen.queryByTestId("agent-popup")).toBeNull();

    act(() => {
      fakeMap.fire!("contextmenu", {
        preventDefault: () => {},
        point: { x: 10, y: 10 },
        lngLat: { lng: -120, lat: 46 },
      });
    });
    expect(screen.getByTestId("agent-popup")).toBeTruthy();
  });

  it("opens the AI popup through a drought fill on a fine pointer, and stands down on a coarse one", () => {
    fakeMap.features = [{ layer: { id: "earth" } }, { layer: { id: "drought-fill" } }];
    window.matchMedia = vi.fn().mockReturnValue({ matches: false }) as unknown as typeof window.matchMedia;
    const fine = render(<MapView />);
    clickMap();
    expect(screen.getByTestId("agent-popup")).toBeTruthy();
    fine.unmount();

    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia;
    render(<MapView />);
    clickMap();
    expect(screen.queryByTestId("agent-popup")).toBeNull();
    // @ts-expect-error -- jsdom implements no matchMedia by default; undo the per-test stub.
    delete window.matchMedia;
  });

  it("stands down for a botanical occurrence dot on a fine pointer, which has its own click handler", () => {
    render(<MapView />);
    fakeMap.features = [{ layer: { id: "earth" } }, { layer: { id: "botanical-occurrences-exact" } }];
    clickMap();
    expect(screen.queryByTestId("agent-popup")).toBeNull();
  });

  it("stands down for a GBIF occurrence dot, which has its own click handler", () => {
    render(<MapView />);
    fakeMap.features = [{ layer: { id: "earth" } }, { layer: { id: "gbif-occurrences-generalized" } }];
    clickMap();
    expect(screen.queryByTestId("agent-popup")).toBeNull();
  });

  it("stands down even while the intervention layers are inspection-suppressed", () => {
    render(<MapView />);
    setScalarFieldInspectionSuppressed(
      fakeMap.instance!,
      [...INTERVENTION_STYLE_LAYER_IDS],
      true
    );
    fakeMap.features = INTERVENTION_STYLE_LAYER_IDS.map((id) => ({
      layer: { id },
    }));

    clickMap();

    expect(screen.queryByTestId("agent-popup")).toBeNull();
  });
});
