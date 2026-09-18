import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import { LandContextLayer } from "@/components/map/land-context/LandContextLayer";
import {
  useLandContextStore,
  type LandContextFeature,
  type LandContextGroupId,
} from "@/stores/land-context-store";
import { useMapStore } from "@/stores/map-store";
import { INTERVENTION_STYLE_LAYER_IDS } from "@/lib/map/layer-registry";

/**
 * The selection deadlock and the once-per-map listener discipline.
 *
 * Before 2026-09-15 the only map writer of `setSelection` was a click listener delegated to the
 * land-context fill layers -- which only have features once a selection exists. These tests pin
 * the way out (a bare canvas click, active only while a group is on) and that a click on a drawn
 * feature still focuses that exact candidate without discarding the selection it came from.
 *
 * The fixture records listeners by type AND layer id in an ARRAY, not a set: a set would silently
 * deduplicate and hide a duplicate-handler regression (see `src/components/map/AGENTS.md`).
 */
type Listener = (...args: unknown[]) => void;
interface RecordedListener {
  type: string;
  layerId: string | null;
  handler: Listener;
}

function mapFixture() {
  const layers = new Map<string, LayerSpecification>();
  const sources = new Map<
    string,
    { data: GeoJSON.FeatureCollection; setData: ReturnType<typeof vi.fn> }
  >();
  const listeners: RecordedListener[] = [];
  // Two answers: the land-context pick asks with `{ layers }` and gets `renderedHits`; the
  // ownership check asks with no options and gets `foreignHits` (features of other surfaces).
  let renderedHits: { properties: Record<string, unknown> }[] = [];
  let foreignHits: { layer: { id: string } }[] = [];

  const map = {
    getStyle: () => ({ version: 8, sources: {}, layers: [...layers.values()] }),
    isStyleLoaded: () => true,
    getLayer: (id: string) => layers.get(id),
    getSource: (id: string) => sources.get(id),
    addLayer: vi.fn((layer: LayerSpecification) => {
      layers.set(layer.id, layer);
    }),
    addSource: vi.fn((id: string, input: { data: GeoJSON.FeatureCollection }) => {
      const source = {
        data: input.data,
        setData: vi.fn((data: GeoJSON.FeatureCollection) => {
          source.data = data;
        }),
      };
      sources.set(id, source);
    }),
    removeLayer: vi.fn((id: string) => layers.delete(id)),
    removeSource: vi.fn((id: string) => sources.delete(id)),
    setLayoutProperty: vi.fn(),
    queryRenderedFeatures: vi.fn((_point: unknown, options?: { layers?: string[] }) =>
      options?.layers ? renderedHits : foreignHits
    ),
    on: vi.fn((type: string, layerIdOrHandler: string | Listener, maybeHandler?: Listener) => {
      const handler = maybeHandler ?? (layerIdOrHandler as Listener);
      const layerId = typeof layerIdOrHandler === "string" ? layerIdOrHandler : null;
      listeners.push({ type, layerId, handler });
    }),
    off: vi.fn((type: string, layerIdOrHandler: string | Listener, maybeHandler?: Listener) => {
      const handler = maybeHandler ?? (layerIdOrHandler as Listener);
      const layerId = typeof layerIdOrHandler === "string" ? layerIdOrHandler : null;
      const index = listeners.findIndex(
        (entry) => entry.type === type && entry.layerId === layerId && entry.handler === handler
      );
      if (index !== -1) listeners.splice(index, 1);
    }),
  };

  return {
    map: map as unknown as MapLibreMap,
    calls: map,
    layers,
    sources,
    listeners,
    setRenderedHits(hits: { properties: Record<string, unknown> }[]) {
      renderedHits = hits;
    },
    setForeignHits(hits: { layer: { id: string } }[]) {
      foreignHits = hits;
    },
    fire(type: string, event: unknown, layerId: string | null = null) {
      for (const entry of [...listeners]) {
        if (entry.type === type && entry.layerId === layerId) entry.handler(event);
      }
    },
    bareListeners(type: string) {
      return listeners.filter((entry) => entry.type === type && entry.layerId === null);
    },
    delegatedListeners(type: string) {
      return listeners.filter((entry) => entry.type === type && entry.layerId !== null);
    },
  };
}

const ALL_OFF: Record<LandContextGroupId, boolean> = {
  "parcels-land-use": false,
  "electric-utility-territories": false,
  "blm-lands": false,
  "state-managed-lands": false,
};

function resetStore(overrides: Partial<ReturnType<typeof useLandContextStore.getState>> = {}) {
  useLandContextStore.setState({
    enabledGroups: { ...ALL_OFF },
    selection: null,
    results: [],
    resultMeta: null,
    candidateIndex: null,
    hoveredFeature: null,
    hoverPosition: null,
    panelOpen: false,
    queryStatus: "idle",
    ...overrides,
  });
}

function feature(id: string, group: LandContextGroupId): LandContextFeature {
  return {
    id,
    group,
    title: id,
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [-116.3, 43.5],
          [-116.1, 43.5],
          [-116.1, 43.7],
          [-116.3, 43.5],
        ],
      ],
    },
  };
}

const CLICK = { point: { x: 10, y: 10 }, lngLat: { lng: -116.2, lat: 43.6 } };
// The basemap's unfiltered fills, under every land/water pixel in production (styles.ts).
const EARTH = { layer: { id: "earth" } };
const WATER = { layer: { id: "water" } };
const AREA_SELECTION = {
  mode: "area" as const,
  areaPolygon: [
    [-116.4, 43.4],
    [-116.0, 43.4],
    [-116.0, 43.8],
    [-116.4, 43.8],
    [-116.4, 43.4],
  ],
};

beforeEach(() => {
  resetStore();
  useMapStore.setState({ isCapturingQueryPoint: false });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LandContextLayer breaks the selection deadlock", () => {
  it("makes a point selection at a bare canvas click while a group is on -- the click, never the viewport centre", () => {
    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);

    act(() => fixture.fire("click", CLICK));

    expect(useLandContextStore.getState().selection).toEqual({
      mode: "point",
      point: [-116.2, 43.6],
    });
    // No candidate is focused yet, so the panel is not pinned: the first overlap is never chosen
    // for the user (spec), and the drawn features / accessible list are the candidate pickers.
    expect(useLandContextStore.getState().candidateIndex).toBeNull();
    expect(useLandContextStore.getState().panelOpen).toBe(false);
  });

  it("ignores a bare canvas click while every group is off", () => {
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);

    act(() => fixture.fire("click", CLICK));

    expect(useLandContextStore.getState().selection).toBeNull();
    // Neither the ownership check nor the pick runs: the group gate comes first.
    expect(fixture.calls.queryRenderedFeatures).not.toHaveBeenCalled();
  });

  it("focuses the clicked drawn feature precisely and keeps the selection it belongs to", () => {
    const first = feature("blm:SMA-1:0", "blm-lands");
    const second = feature("blm:SMA-2:1", "blm-lands");
    resetStore({
      enabledGroups: { ...ALL_OFF, "blm-lands": true },
      selection: AREA_SELECTION,
      results: [first, second],
    });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    fixture.setRenderedHits([{ properties: { featureId: second.id } }]);
    fixture.setForeignHits([EARTH]);

    act(() => fixture.fire("click", CLICK));

    const state = useLandContextStore.getState();
    expect(state.candidateIndex).toBe(1);
    expect(state.panelOpen).toBe(true);
    // The area selection survives a feature click: results are not wiped and the project area is
    // not collapsed to the clicked point.
    expect(state.selection).toEqual(AREA_SELECTION);
    expect(state.results).toEqual([first, second]);
    // Only the fill layers that exist are queried -- a missing id would make MapLibre fire `error`.
    expect(fixture.calls.queryRenderedFeatures).toHaveBeenCalledWith(CLICK.point, {
      layers: [
        "land-context-fill-parcels-land-use",
        "land-context-fill-electric-utility-territories",
        "land-context-fill-blm-lands",
        "land-context-fill-state-managed-lands",
      ],
    });
  });

  it("falls back to a point selection when the hit feature is no longer in the results", () => {
    resetStore({
      enabledGroups: { ...ALL_OFF, "blm-lands": true },
      selection: AREA_SELECTION,
      results: [feature("blm:SMA-1:0", "blm-lands")],
    });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    fixture.setRenderedHits([{ properties: { featureId: "blm:STALE:9" } }]);
    fixture.setForeignHits([EARTH]);

    act(() => fixture.fire("click", CLICK));

    expect(useLandContextStore.getState().selection).toEqual({
      mode: "point",
      point: [-116.2, 43.6],
    });
  });

  it("stands down while a panel is capturing query points (one click, one meaning)", () => {
    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    useMapStore.setState({ isCapturingQueryPoint: true });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);

    act(() => fixture.fire("click", CLICK));

    expect(useLandContextStore.getState().selection).toBeNull();
  });

  it("stands down when the click landed on a feature another surface owns", () => {
    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);

    fixture.setForeignHits([EARTH, { layer: { id: INTERVENTION_STYLE_LAYER_IDS[0] } }]);
    act(() => fixture.fire("click", CLICK));
    expect(useLandContextStore.getState().selection).toBeNull();

    // A dedicated-popup layer (fire/water/GBIF) owns its click on any pointer.
    fixture.setForeignHits([EARTH, { layer: { id: "gbif-occurrences-exact" } }]);
    act(() => fixture.fire("click", CLICK));
    expect(useLandContextStore.getState().selection).toBeNull();

    // A botanical point has its own click handler too (fine pointer here: jsdom has no matchMedia).
    fixture.setForeignHits([EARTH, { layer: { id: "botanical-occurrences-possible" } }]);
    act(() => fixture.fire("click", CLICK));
    expect(useLandContextStore.getState().selection).toBeNull();
  });

  it("selects through a drought fill on a fine pointer, but not on a coarse one (HoverTooltip's tap gate)", () => {
    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    fixture.setForeignHits([EARTH, { layer: { id: "drought-fill" } }]);

    window.matchMedia = vi.fn().mockReturnValue({ matches: false }) as unknown as typeof window.matchMedia;
    act(() => fixture.fire("click", CLICK));
    expect(useLandContextStore.getState().selection).toEqual({ mode: "point", point: [-116.2, 43.6] });

    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia;
    act(() => fixture.fire("click", CLICK));
    expect(useLandContextStore.getState().selection).toBeNull();
    // @ts-expect-error -- jsdom implements no matchMedia by default; undo the per-test stub.
    delete window.matchMedia;
  });

  it.each([EARTH, WATER])("still selects over the basemap fill %o -- ground owns nothing", (fill) => {
    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    fixture.setForeignHits([fill]);

    act(() => fixture.fire("click", CLICK));

    expect(useLandContextStore.getState().selection).toEqual({
      mode: "point",
      point: [-116.2, 43.6],
    });
  });

  it("lets a drawn land-context feature win before any ownership check, even over an owning layer", () => {
    const only = feature("blm:SMA-1:0", "blm-lands");
    resetStore({
      enabledGroups: { ...ALL_OFF, "blm-lands": true },
      selection: AREA_SELECTION,
      results: [only],
    });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    fixture.setRenderedHits([{ properties: { featureId: only.id } }]);
    // Ground AND a gauge popup layer under the same pixel: the drawn feature is still the meaning.
    fixture.setForeignHits([EARTH, { layer: { id: "water-gauges-circle" } }]);

    act(() => fixture.fire("click", CLICK));

    const state = useLandContextStore.getState();
    expect(state.candidateIndex).toBe(0);
    expect(state.panelOpen).toBe(true);
    expect(state.selection).toEqual(AREA_SELECTION);
    // The ownership check never ran: only the `{ layers }` pick queried.
    expect(fixture.calls.queryRenderedFeatures).toHaveBeenCalledTimes(1);
  });

  it("does not query rendered features while no land-context layer exists in the style", () => {
    resetStore({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    // A style swap has wiped the custom layers and `style.load` has not re-added them yet.
    fixture.layers.clear();
    fixture.setForeignHits([EARTH]);

    act(() => fixture.fire("click", CLICK));

    // The ownership check still asks (no `layers` option); the land-context pick never does.
    expect(fixture.calls.queryRenderedFeatures).toHaveBeenCalledTimes(1);
    expect(fixture.calls.queryRenderedFeatures).toHaveBeenCalledWith(CLICK.point);
    expect(useLandContextStore.getState().selection).toEqual({
      mode: "point",
      point: [-116.2, 43.6],
    });
  });
});

describe("LandContextLayer registers its handlers once per map", () => {
  it("keeps one bare click listener and one style.load listener across toggle and result changes", () => {
    const fixture = mapFixture();
    const mounted = render(<LandContextLayer map={fixture.map} />);

    expect(fixture.bareListeners("click")).toHaveLength(1);
    expect(fixture.bareListeners("style.load")).toHaveLength(1);
    expect(fixture.delegatedListeners("mousemove")).toHaveLength(4);
    expect(fixture.delegatedListeners("mouseleave")).toHaveLength(4);
    // The feature-click path is folded into the bare listener; no delegated click remains.
    expect(fixture.delegatedListeners("click")).toHaveLength(0);
    const [clickHandler] = fixture.bareListeners("click");
    const [styleHandler] = fixture.bareListeners("style.load");

    act(() => {
      useLandContextStore.setState({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    });
    act(() => {
      useLandContextStore.setState({
        selection: { mode: "point", point: [-116.2, 43.6] },
        results: [feature("blm:SMA-1:0", "blm-lands")],
      });
    });
    mounted.rerender(<LandContextLayer map={fixture.map} />);

    expect(fixture.bareListeners("click")).toEqual([clickHandler]);
    expect(fixture.bareListeners("style.load")).toEqual([styleHandler]);
    expect(fixture.delegatedListeners("mousemove")).toHaveLength(4);
    expect(fixture.calls.off).not.toHaveBeenCalledWith("click", expect.any(Function));
    expect(fixture.calls.off).not.toHaveBeenCalledWith("style.load", expect.any(Function));

    mounted.unmount();

    expect(fixture.listeners).toHaveLength(0);
    expect(fixture.layers.size).toBe(0);
    expect(fixture.sources.size).toBe(0);
  });

  it("reads the store at click time rather than closing over it", () => {
    const fixture = mapFixture();
    render(<LandContextLayer map={fixture.map} />);
    const [clickHandler] = fixture.bareListeners("click");

    act(() => fixture.fire("click", CLICK));
    expect(useLandContextStore.getState().selection).toBeNull();

    act(() => {
      useLandContextStore.setState({ enabledGroups: { ...ALL_OFF, "state-managed-lands": true } });
    });
    act(() => fixture.fire("click", CLICK));

    expect(useLandContextStore.getState().selection).toEqual({
      mode: "point",
      point: [-116.2, 43.6],
    });
    expect(fixture.bareListeners("click")).toEqual([clickHandler]);
  });
});
