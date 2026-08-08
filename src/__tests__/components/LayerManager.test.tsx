import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MapProvider } from "@/lib/map/map-context";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import { UNINITIALIZED_DATE, useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";
import type maplibregl from "maplibre-gl";

const dynamicStub = vi.hoisted(() => ({
  /** Every sub-layer render this file has seen, newest last. */
  renders: [] as { component: string; props: Record<string, unknown> }[],
}));

/**
 * LayerManager's own style-readiness wiring (applyVisibility / styleBackedLayerEntries,
 * see src/lib/map/layer-registry.ts) is what is under test here, not any of its
 * dynamically-imported sub-layers -- stubbing next/dynamic keeps the fake map below from
 * needing to satisfy FireLayer/WaterLayer/etc.'s own addSource/addLayer calls too.
 *
 * The stub still records which layer it stood in for and what props it received, because
 * "the layer is never mounted / never fed" is itself a defect class this file guards. The
 * loader's source text is the only handle on identity a stubbed dynamic import has: the
 * module path survives the transform, so `layers/WaterLayer` is recoverable from it.
 */
vi.mock("next/dynamic", () => ({
  default: (loader: unknown) => {
    const component = /layers[/\\](\w+)/.exec(String(loader))?.[1] ?? "unknown";
    return function DynamicLayerStub(props: Record<string, unknown>) {
      dynamicStub.renders.push({ component, props });
      return null;
    };
  },
}));

/** The newest props a given sub-layer was rendered with, or null if it never rendered. */
function lastRenderOf(component: string): Record<string, unknown> | null {
  for (let i = dynamicStub.renders.length - 1; i >= 0; i--) {
    if (dynamicStub.renders[i].component === component) return dynamicStub.renders[i].props;
  }
  return null;
}

vi.mock("@/hooks/useFireData", () => ({
  useFireData: () => ({
    data: { type: "FeatureCollection", features: [] },
    count: 0,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

type ViewportQueryResult = { data: GeoJSON.FeatureCollection | undefined };
type StreamflowQueryResult = { data: unknown[] };

const viewportQueries = vi.hoisted(() => ({
  // Declared with no parameters on purpose: both arguments are still recorded on
  // `mock.calls`, which is where the `enabled` flag and the query input are read from.
  getWatersheds: vi.fn((): ViewportQueryResult => ({ data: undefined })),
  getSoilSurvey: vi.fn((): ViewportQueryResult => ({ data: undefined })),
  getSoilField: vi.fn((): ViewportQueryResult => ({ data: undefined })),
  getClimateField: vi.fn((): ViewportQueryResult => ({ data: undefined })),
  // react-query's `enabled: false` does not evict a cached result -- see the negative
  // test below -- so this is mutable per-test rather than a static `vi.fn(() => ...)`.
  getStreamflow: vi.fn((): StreamflowQueryResult => ({ data: [] })),
  getGroundwater: vi.fn((): StreamflowQueryResult => ({ data: [] })),
  getVegetationIndex: vi.fn((): ViewportQueryResult => ({ data: undefined })),
  getDroughtClassification: vi.fn((): ViewportQueryResult => ({ data: undefined })),
  getWeatherForBbox: vi.fn((): StreamflowQueryResult => ({ data: [] })),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: {
      getDroughtClassification: { useQuery: viewportQueries.getDroughtClassification },
      getStreamflow: { useQuery: viewportQueries.getStreamflow },
      getGroundwater: { useQuery: viewportQueries.getGroundwater },
      getWatersheds: { useQuery: viewportQueries.getWatersheds },
      getSoilSurvey: { useQuery: viewportQueries.getSoilSurvey },
      getSoilField: { useQuery: viewportQueries.getSoilField },
      getClimateField: { useQuery: viewportQueries.getClimateField },
      getVegetationIndex: { useQuery: viewportQueries.getVegetationIndex },
    },
    wildfire: {
      getWeatherForBbox: { useQuery: viewportQueries.getWeatherForBbox },
    },
  },
}));

/** A one-polygon stand-in for either viewport-proxied collection. */
function polygonCollection(): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [-116.3, 43.5],
              [-116.3, 43.7],
              [-116.1, 43.7],
              [-116.3, 43.5],
            ],
          ],
        },
        properties: { name: "Cottonwood Creek-Shafer Creek", huc12: "170501220201" },
      },
    ],
  };
}

/** The `enabled` flag the component passed for a viewport-proxied query. */
function enabledFlagOf(query: { mock: { calls: unknown[][] } }): boolean | undefined {
  const lastCall = query.mock.calls.at(-1);
  return (lastCall?.[1] as { enabled?: boolean } | undefined)?.enabled;
}

/** The input the component passed for a query. */
function inputOf(query: { mock: { calls: unknown[][] } }): unknown {
  return query.mock.calls.at(-1)?.[0];
}

/**
 * The input for ONE soil field. Both fields go through the same procedure, so `inputOf`
 * would always report whichever measure LayerManager renders last.
 */
function soilFieldInputOf(measure: "moisture" | "temperature"): Record<string, unknown> {
  const calls = (viewportQueries.getSoilField.mock.calls as unknown as unknown[][]).filter(
    (call) => (call[0] as { measure?: string } | undefined)?.measure === measure
  );
  return (calls.at(-1)?.[0] ?? {}) as Record<string, unknown>;
}

/** The `enabled` flag for ONE soil field, matched the same way. */
function soilFieldEnabledFlagOf(measure: "moisture" | "temperature"): boolean | undefined {
  const calls = (viewportQueries.getSoilField.mock.calls as unknown as unknown[][]).filter(
    (call) => (call[0] as { measure?: string } | undefined)?.measure === measure
  );
  return (calls.at(-1)?.[1] as { enabled?: boolean } | undefined)?.enabled;
}

import LayerManager from "@/components/map/LayerManager";

/**
 * A minimal maplibre-gl Map stand-in: an event emitter plus the handful of methods
 * LayerManager's effects call. isStyleLoaded() is driven by a settable flag, independent
 * of firing "style.load"/"styledata", so a test can reproduce style.load firing before
 * sources have finished loading -- exactly the gap styleReady exists to close.
 */
function createFakeMap() {
  const listeners = new Map<string, Set<() => void>>();
  // Counted cumulatively, never decremented: the property under test is that a handler is
  // registered ONCE per map and keeps its place in the queue, so an off/on pair is exactly
  // the regression to catch, not an even trade.
  const registrations = new Map<string, number>();
  let styleLoaded = false;

  return {
    on: (type: string, handler: () => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
      registrations.set(type, (registrations.get(type) ?? 0) + 1);
    },
    registrationCountFor: (type: string) => registrations.get(type) ?? 0,
    off: (type: string, handler: () => void) => {
      listeners.get(type)?.delete(handler);
    },
    isStyleLoaded: () => styleLoaded,
    getLayer: () => true,
    setLayoutProperty: vi.fn(),
    // The single writer of every style-baked layer's opacity -- see
    // src/lib/map/layer-opacity.ts. It is always written as the AUTHORED base scaled, never as
    // an absolute, so a factor of 1 rewrites exactly what the style declared.
    setPaintProperty: vi.fn(),
    // Style-baked Martin layers take the slider's day as a style filter rather than as a
    // prop, because they are not React-mounted -- see src/lib/map/tile-layer-date-filter.ts.
    setFilter: vi.fn(),
    setStyleLoaded(value: boolean) {
      styleLoaded = value;
    },
    emit(type: string) {
      for (const handler of Array.from(listeners.get(type) ?? [])) handler();
    },
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

function renderLayerManager(fakeMap: FakeMap) {
  return renderWithProviders(
    <MapProvider value={fakeMap as unknown as maplibregl.Map}>
      <LayerManager />
    </MapProvider>
  );
}

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_SOIL_STATE = useSoilStore.getState();
const INITIAL_LAYER_STATE = useLayerStore.getState();

const SERVER_CURRENT_DATE = "2026-08-04";
const sliderCapabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  layers: [
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2026-05-24",
    },
  ],
};

/** No day selected and no capabilities: what every case here but the date ones assumes. */
function resetSliderStore() {
  useTimeSliderStore.setState({
    selectedDate: UNINITIALIZED_DATE,
    forecastVariant: "monte_carlo",
    capabilities: null,
  });
}

beforeEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
  // Sparse and empty by default -- an absent key means "as authored", so no case here starts
  // with a layer already dimmed by a previous one.
  useLayerStore.setState({ ...INITIAL_LAYER_STATE, layerOpacity: {} }, true);
  resetSliderStore();
  dynamicStub.renders.length = 0;
  viewportQueries.getWatersheds.mockReturnValue({ data: undefined });
  viewportQueries.getSoilSurvey.mockReturnValue({ data: undefined });
  viewportQueries.getSoilField.mockReturnValue({ data: undefined });
  viewportQueries.getClimateField.mockReturnValue({ data: undefined });
  viewportQueries.getStreamflow.mockReturnValue({ data: [] });
  viewportQueries.getGroundwater.mockReturnValue({ data: [] });
  viewportQueries.getVegetationIndex.mockReturnValue({ data: undefined });
  viewportQueries.getDroughtClassification.mockReturnValue({ data: undefined });
  viewportQueries.getWeatherForBbox.mockReturnValue({ data: [] });
});

afterEach(() => {
  vi.clearAllMocks();
  dynamicStub.renders.length = 0;
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
  useLayerStore.setState({ ...INITIAL_LAYER_STATE, layerOpacity: {} }, true);
  // The slider store is reset in beforeEach only: writing it here would fire
  // useDebouncedMapDay's subscription while the tree is still mounted, scheduling a settle
  // timer that lands after the test and outside act().
});

describe("LayerManager style readiness", () => {
  it("re-applies visibility once the style catches up, not just when style.load fires", () => {
    // `sensors` is style-backed with a single style layer id and no governance reason, so
    // applyVisibility is the only thing deciding its visibility. (This was
    // `building-footprints` until the registry withheld that one for having an empty backing
    // table -- a withheld toggle reads false in useLayerVisibility whatever activeLayers says,
    // which would make every case here pass for the wrong reason.)
    useMapStore.setState({ activeLayers: [] });
    const fakeMap = createFakeMap();
    renderLayerManager(fakeMap);

    // style.load fires while sources are still loading -- isStyleLoaded() reads false.
    act(() => {
      fakeMap.emit("style.load");
    });

    // The toggle flips on while the style is still mid-load (e.g. a persisted toggle
    // hydrating). Before the fix, the guarded effect ran once here, saw
    // isStyleLoaded() === false, no-opped, and was never retried.
    act(() => {
      useMapStore.setState({ activeLayers: ["sensors"] });
    });
    expect(fakeMap.setLayoutProperty).not.toHaveBeenCalledWith(
      "sensors",
      "visibility",
      "visible"
    );

    // Sources land; only styledata fires here (style.load already fired once for this
    // style and will not fire again until a swap), and isStyleLoaded() now reads true.
    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });

    expect(fakeMap.setLayoutProperty).toHaveBeenCalledWith(
      "sensors",
      "visibility",
      "visible"
    );
  });

  it("never applies visible for a toggle that stays off, even after the style catches up", () => {
    useMapStore.setState({ activeLayers: [] });
    const fakeMap = createFakeMap();
    renderLayerManager(fakeMap);

    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("style.load");
      fakeMap.emit("styledata");
    });

    const visibleCalls = fakeMap.setLayoutProperty.mock.calls.filter(
      ([, , value]) => value === "visible"
    );
    expect(visibleCalls).toHaveLength(0);
  });

  it("re-applies visibility to the new style after a basemap swap, not just on first load", () => {
    useMapStore.setState({ activeLayers: ["sensors"] });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    // Mount alone already applies once, since isStyleLoaded() reads true immediately.
    expect(fakeMap.setLayoutProperty).toHaveBeenCalledWith(
      "sensors",
      "visibility",
      "visible"
    );
    fakeMap.setLayoutProperty.mockClear();

    // A basemap swap: setState()'s diff path fires style.load synchronously, and the
    // new style's sources have not loaded yet, so isStyleLoaded() reads false again.
    act(() => {
      fakeMap.setStyleLoaded(false);
      fakeMap.emit("style.load");
    });
    // The persistent listener's direct applyVisibility call is the basemap-swap safety
    // net and fires unconditionally on every style.load -- this is what already made
    // "switch the basemap" work before this fix, and must keep working after it.
    expect(fakeMap.setLayoutProperty).toHaveBeenCalledWith(
      "sensors",
      "visibility",
      "visible"
    );
    fakeMap.setLayoutProperty.mockClear();

    // The new style's sources land; isStyleLoaded() flips true and the guarded effect
    // (this fix) re-applies too, so the new style is never left relying on the safety
    // net alone.
    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });
    expect(fakeMap.setLayoutProperty).toHaveBeenCalledWith(
      "sensors",
      "visibility",
      "visible"
    );
  });
});

/**
 * Per-layer opacity is a MULTIPLIER over each layer's authored paint, applied by LayerManager
 * for style-baked layers and threaded as `opacityScale` into every component-mounted one --
 * one writer per (layer, paint property). See src/lib/map/layer-opacity.ts.
 */
describe("LayerManager applies per-layer opacity", () => {
  /** The value written for one (style layer, property) pair, or undefined if never written. */
  function paintWriteFor(fakeMap: FakeMap, layerId: string, property: string): unknown {
    const calls = fakeMap.setPaintProperty.mock.calls.filter(
      ([writtenLayerId, writtenProperty]) =>
        writtenLayerId === layerId && writtenProperty === property
    );
    return calls.at(-1)?.[2];
  }

  it("writes each style-baked layer's AUTHORED base when nothing has been dimmed", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    act(() => {
      fakeMap.emit("style.load");
    });

    // The watershed pair in one toggle: 0.05 for the boundary wash, 0.8 for its outline. An
    // absolute slider would need a different neutral position for each; a multiplier of 1
    // rewrites exactly what layers.ts declared.
    expect(paintWriteFor(fakeMap, "watersheds-fill", "fill-opacity")).toBe(0.05);
    expect(paintWriteFor(fakeMap, "watersheds-outline", "line-opacity")).toBe(0.8);
    expect(paintWriteFor(fakeMap, "fire-perimeters", "fill-opacity")).toBe(0.5);
  });

  it("scales the authored base, rather than writing the reader's number as an absolute", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    act(() => {
      useLayerStore.getState().setLayerOpacity("watersheds", 0.5);
    });
    act(() => {
      fakeMap.emit("style.load");
    });

    expect(paintWriteFor(fakeMap, "watersheds-fill", "fill-opacity")).toBeCloseTo(0.025, 6);
    expect(paintWriteFor(fakeMap, "watersheds-outline", "line-opacity")).toBeCloseTo(0.4, 6);
  });

  it("uses the property that layer's TYPE carries, not one shared property", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    act(() => {
      fakeMap.emit("style.load");
    });

    const written = new Set(
      fakeMap.setPaintProperty.mock.calls.map(([layerId, property]) => `${layerId}|${property}`)
    );
    // fill / line / circle / fill-extrusion, each on the layer whose type defines it.
    expect(written).toContain("interventions|fill-opacity");
    expect(written).toContain("interventions-outline|line-opacity");
    expect(written).toContain("interventions-points|circle-opacity");
    expect(written).toContain("building-footprints|fill-extrusion-opacity");
    // ...and never a property the layer's type does not define.
    expect(written).not.toContain("interventions|circle-opacity");
    expect(written).not.toContain("interventions-points|fill-opacity");
  });

  /**
   * The trap this pins: LayerManager's `style.load` handler is registered ONCE per map so it
   * keeps its place in the listener queue -- ServiceAreaLayer mounts first so its dimming mask
   * stays beneath the data pins (src/components/map/AGENTS.md "Style.load listener order").
   * Putting the opacity record in that effect's deps would re-register the handler on nearly
   * every pointer tick of a slider drag and drop the mask on top of everything.
   */
  it("does not re-register the style.load listener when a layer is dimmed", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    const registrationsBefore = fakeMap.registrationCountFor("style.load");

    act(() => {
      useLayerStore.getState().setLayerOpacity("watersheds", 0.4);
    });
    act(() => {
      useLayerStore.getState().setLayerOpacity("watersheds", 0.35);
    });

    expect(fakeMap.registrationCountFor("style.load")).toBe(registrationsBefore);
  });

  // Every component-mounted layer folds the multiplier in itself, because five of them
  // already own setPaintProperty on the same properties and two rewrite on every pan. The
  // three soil toggles take three independent scalars where one `soil-store.opacity` used to
  // drive the SoilGrids raster and both ERA5-Land fields at once.
  it("threads a separate scalar into every component-mounted layer", () => {
    useMapStore.setState({
      activeLayers: ["fire", "soil", "soil-moisture", "soil-temperature"],
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    act(() => {
      useLayerStore.getState().setLayerOpacity("soil-moisture", 0.4);
    });

    const soilFieldRenders = dynamicStub.renders.filter(
      (render) => render.component === "SoilFieldLayer"
    );
    const moisture = soilFieldRenders.filter((r) => r.props.measure === "moisture").at(-1);
    const temperature = soilFieldRenders.filter((r) => r.props.measure === "temperature").at(-1);

    expect(moisture?.props.opacityScale).toBe(0.4);
    expect(temperature?.props.opacityScale).toBe(1);
    expect(lastRenderOf("SoilLayer")?.opacityScale).toBe(1);
    // Both props LayerManager declared but never passed before this landed, so FireLayer's
    // and DroughtLayer's live opacity effects were inert.
    expect(lastRenderOf("FireLayer")?.opacityScale).toBe(1);
    expect(lastRenderOf("DroughtLayer")?.opacityScale).toBe(1);
    // And never an absolute `opacity` -- the component owns its authored base.
    expect(lastRenderOf("FireLayer")).not.toHaveProperty("opacity");
    expect(moisture?.props).not.toHaveProperty("opacity");
  });
});

/**
 * `soil-survey` is a component-rendered toggle (renderKind "component", no style layer
 * ids), so `applyVisibility` above can never draw it -- only a mounted, fed sub-layer can.
 * Its endpoint existed with no consumer at all, which is exactly the failure these cases
 * pin down: a registry entry and a live tRPC procedure that nothing ever reads still render
 * nothing. `watersheds` sat here too until it moved onto geo.watershed_tiles(); it is now
 * style-backed and its case below asserts the setLayoutProperty path instead.
 */
describe("LayerManager viewport-proxied polygon layers", () => {
  // The bug this pins: while watersheds was proxied, environmental.getWatersheds capped a
  // request at 1 square degree against a ~767 sq-deg viewport bbox, so every request 400d
  // and the layer drew an empty collection at every ordinary zoom. It is now baked into the
  // style, so LayerManager must reach it with setLayoutProperty and must not proxy it at all.
  it("flips the baked watershed style layers on instead of proxying a collection", () => {
    useMapStore.setState({ activeLayers: ["watersheds"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    for (const layerId of ["watersheds-fill", "watersheds-outline"]) {
      expect(fakeMap.setLayoutProperty).toHaveBeenCalledWith(layerId, "visibility", "visible");
    }
    // The proxy is no longer on the map's path at all -- not merely disabled.
    expect(viewportQueries.getWatersheds).not.toHaveBeenCalled();
    // WaterLayer stays mounted for the gauge/well points, but the separate "water" toggle
    // is off, so it must not draw them.
    expect(lastRenderOf("WaterLayer")?.visible).toBe(false);
  });

  it("leaves the baked watershed style layers hidden while the toggle is off", () => {
    useMapStore.setState({ activeLayers: ["water"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    for (const layerId of ["watersheds-fill", "watersheds-outline"]) {
      expect(fakeMap.setLayoutProperty).toHaveBeenCalledWith(layerId, "visibility", "none");
      expect(fakeMap.setLayoutProperty).not.toHaveBeenCalledWith(
        layerId,
        "visibility",
        "visible"
      );
    }
  });

  it("does not render gauge points when watersheds is on and water is off, even if the streamflow cache still holds a gauge fetched earlier", () => {
    // Reproduces the concrete failure: react-query's `enabled: false` does not evict the
    // cache, so `streamflowQuery.data` can still hold the gauge array from when "water"
    // was previously on. Before the fix, LayerManager passed WaterLayer
    // `visible={waterEnabled || watershedsVisible}`, which read true here purely because
    // watersheds is on -- rendering gauge circles for measurements the user had
    // explicitly turned off, with no toggle claiming them. Watersheds no longer touches
    // WaterLayer at all, which is the structural version of the same guarantee.
    viewportQueries.getStreamflow.mockReturnValue({
      data: [
        {
          siteNo: "13172500",
          siteName: "Boise River",
          lon: -116.2,
          lat: 43.6,
          flowCfs: 500,
          percentile: 40,
          condition: "normal",
        },
      ],
    });
    useMapStore.setState({ activeLayers: ["watersheds"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    const waterProps = lastRenderOf("WaterLayer");
    expect(waterProps).not.toBeNull();
    // The stale gauge is still handed through as `gauges`...
    expect((waterProps?.gauges as unknown[])?.length).toBeGreaterThan(0);
    // ...but `visible` -- the prop that actually gates whether WaterLayer draws gauge
    // circles -- must be false, since the "water" toggle itself is off.
    expect(waterProps?.visible).toBe(false);
  });

  it("mounts SoilSurveyLayer with the SSURGO collection when the soil-survey toggle is on", () => {
    const collection = polygonCollection();
    viewportQueries.getSoilSurvey.mockReturnValue({ data: collection });
    useMapStore.setState({ activeLayers: ["soil-survey"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    const soilSurveyProps = lastRenderOf("SoilSurveyLayer");
    expect(soilSurveyProps).not.toBeNull();
    expect(soilSurveyProps?.visible).toBe(true);
    expect(soilSurveyProps?.geojson).toBe(collection);
    expect(enabledFlagOf(viewportQueries.getSoilSurvey)).toBe(true);
  });

  it("neither queries nor draws the polygon feed while every toggle is off", () => {
    useMapStore.setState({ activeLayers: [] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(enabledFlagOf(viewportQueries.getSoilSurvey)).toBe(false);
    expect(lastRenderOf("WaterLayer")?.visible).toBe(false);
    expect(lastRenderOf("SoilSurveyLayer")?.visible).toBe(false);
  });

  /**
   * The bug this pins. `resolveSoilSurveyGranularity(undefined)` falls to the detail tier,
   * whose 0.02 sq-deg ceiling the tRPC input then enforces -- so a zoomless request is
   * rejected at any ordinary zoom and the panel shows its "zoom in" note. The server's
   * zoom-adaptive path existed and no caller ever reached it. Same for soil moisture, where
   * zoom selects the server-side aggregation lattice.
   */
  it("sends the live viewport zoom with both zoom-adaptive queries", () => {
    useMapStore.setState({
      activeLayers: ["soil-survey", "soil-moisture"],
      viewport: { longitude: -116.2, latitude: 43.6, zoom: 6.5, bearing: 0, pitch: 0 },
    });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(inputOf(viewportQueries.getSoilSurvey)).toMatchObject({ zoom: 6.5 });
    expect(soilFieldInputOf("moisture")).toMatchObject({ zoom: 6.5 });
  });

  it("moves both zoom-adaptive queries onto a new tier when the viewport zooms", () => {
    useMapStore.setState({
      activeLayers: ["soil-survey", "soil-moisture"],
      viewport: { longitude: -116.2, latitude: 43.6, zoom: 5, bearing: 0, pitch: 0 },
    });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    act(() => {
      useMapStore.setState({
        viewport: { longitude: -116.2, latitude: 43.6, zoom: 14, bearing: 0, pitch: 0 },
      });
    });

    expect(inputOf(viewportQueries.getSoilSurvey)).toMatchObject({ zoom: 14 });
    expect(soilFieldInputOf("moisture")).toMatchObject({ zoom: 14 });
  });

  it("asks for each soil field at that field's own selected depth", () => {
    useMapStore.setState({ activeLayers: ["soil-moisture", "soil-temperature"] });
    useSoilStore.setState({
      fieldDepth: { moisture: "root-zone", temperature: "substratum", vpd: "surface" },
    });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(soilFieldInputOf("moisture")).toMatchObject({
      measure: "moisture",
      depth: "root-zone",
    });
    expect(soilFieldInputOf("temperature")).toMatchObject({
      measure: "temperature",
      depth: "substratum",
    });
  });

  // The two fields are separate switches over one procedure. If `measure` were not in the
  // input, one toggle would answer for both and turning temperature on would silently
  // redraw the moisture field.
  it("enables only the soil field whose own toggle is on", () => {
    useMapStore.setState({ activeLayers: ["soil-temperature"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(soilFieldEnabledFlagOf("temperature")).toBe(true);
    expect(soilFieldEnabledFlagOf("moisture")).toBe(false);
  });
});

/**
 * Every warehouse-backed feed here used to be dateless: the slider reported whether the
 * warehouse held a day and no layer drew it. These cases pin both halves of the fix -- the day
 * reaches every query, and the server's today reaches none of them, because an omitted day and
 * today's date are the same server read and passing the date would mint a second cache entry
 * for the same answer.
 */
describe("LayerManager threads the slider's day into the warehouse-backed queries", () => {
  /** Every procedure whose input carries `date` alongside a bbox. */
  const DATED_BBOX_QUERIES = [
    ["getStreamflow", viewportQueries.getStreamflow],
    ["getGroundwater", viewportQueries.getGroundwater],
    ["getVegetationIndex", viewportQueries.getVegetationIndex],
    ["getSoilField", viewportQueries.getSoilField],
    ["getClimateField", viewportQueries.getClimateField],
    ["getWeatherForBbox", viewportQueries.getWeatherForBbox],
  ] as const;

  function renderAtSelectedDate(selectedDate: string) {
    useTimeSliderStore.setState({
      selectedDate,
      forecastVariant: "monte_carlo",
      capabilities: sliderCapabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);
  }

  it("omits the date at the server's today, so first paint keeps one cache entry per feed", () => {
    renderAtSelectedDate(SERVER_CURRENT_DATE);

    // tRPC keys `undefined` input differently from an object, so the dateless drought call
    // must stay literally undefined rather than becoming `{ date: undefined }`.
    expect(inputOf(viewportQueries.getDroughtClassification)).toBeUndefined();
    for (const [name, query] of DATED_BBOX_QUERIES) {
      const input = inputOf(query) as { bbox?: string; date?: string };
      expect(input.bbox, name).toBeTypeOf("string");
      expect(input.date, name).toBeUndefined();
    }
  });

  it("carries a past day into every one of them", () => {
    renderAtSelectedDate("2026-07-30");

    expect(inputOf(viewportQueries.getDroughtClassification)).toEqual({
      date: "2026-07-30",
    });
    for (const [name, query] of DATED_BBOX_QUERIES) {
      const input = inputOf(query) as { date?: string };
      expect(input.date, name).toBe("2026-07-30");
    }
  });

  it("stays dateless before capabilities name a today, rather than guessing from the browser clock", () => {
    // The default store state: UNINITIALIZED_DATE and no capabilities.
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(inputOf(viewportQueries.getDroughtClassification)).toBeUndefined();
    for (const [name, query] of DATED_BBOX_QUERIES) {
      expect((inputOf(query) as { date?: string }).date, name).toBeUndefined();
    }
  });

  /**
   * The GIBS raster is addressed by a month, so it takes year/month rather than a day. Those
   * came from two sliders of vegetation's own until 2026-08-05; they are now the slider day
   * projected onto a month, and this is the seam where that projection reaches the renderer.
   */
  it("gives the vegetation raster the slider day's own month, not a stored one", () => {
    renderAtSelectedDate("2026-07-30");

    const vegetationProps = lastRenderOf("VegetationLayer");
    expect(vegetationProps?.year).toBe(2026);
    expect(vegetationProps?.month).toBe(7);
  });

  it("gives it no month at all before capabilities name a day", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    // Null, not the browser clock's month: the layer then attaches no raster instead of
    // drawing a composite period nobody selected.
    const vegetationProps = lastRenderOf("VegetationLayer");
    expect(vegetationProps?.year).toBeNull();
    expect(vegetationProps?.month).toBeNull();
  });
});
