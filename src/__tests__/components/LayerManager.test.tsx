import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MapProvider } from "@/lib/map/map-context";
import { useMapStore } from "@/stores/map-store";
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

import LayerManager from "@/components/map/LayerManager";

/**
 * A minimal maplibre-gl Map stand-in: an event emitter plus the handful of methods
 * LayerManager's effects call. isStyleLoaded() is driven by a settable flag, independent
 * of firing "style.load"/"styledata", so a test can reproduce style.load firing before
 * sources have finished loading -- exactly the gap styleReady exists to close.
 */
function createFakeMap() {
  const listeners = new Map<string, Set<() => void>>();
  let styleLoaded = false;

  return {
    on: (type: string, handler: () => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
    },
    off: (type: string, handler: () => void) => {
      listeners.get(type)?.delete(handler);
    },
    isStyleLoaded: () => styleLoaded,
    getLayer: () => true,
    setLayoutProperty: vi.fn(),
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

const SERVER_CURRENT_DATE = "2026-08-04";
const sliderCapabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
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
  resetSliderStore();
  dynamicStub.renders.length = 0;
  viewportQueries.getWatersheds.mockReturnValue({ data: undefined });
  viewportQueries.getSoilSurvey.mockReturnValue({ data: undefined });
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
 * `watersheds` and `soil-survey` are component-rendered toggles (renderKind "component",
 * no style layer ids), so `applyVisibility` above can never draw them -- only a mounted,
 * fed sub-layer can. Both endpoints existed with no consumer at all, which is exactly the
 * failure these cases pin down: a registry entry and a live tRPC procedure that nothing
 * ever reads still render nothing.
 */
describe("LayerManager viewport-proxied polygon layers", () => {
  it("hands WaterLayer the watershed collection when the watersheds toggle is on", () => {
    const collection = polygonCollection();
    viewportQueries.getWatersheds.mockReturnValue({ data: collection });
    useMapStore.setState({ activeLayers: ["watersheds"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    const waterProps = lastRenderOf("WaterLayer");
    expect(waterProps).not.toBeNull();
    // WaterLayer owns the watershed source, so it must stay mounted even though the
    // separate "water" gauge toggle is off -- but `visible` (which gates the gauge/well
    // points) must stay false: only `watershedsVisible` may be true here.
    expect(waterProps?.visible).toBe(false);
    expect(waterProps?.watershedsVisible).toBe(true);
    expect(waterProps?.watershedsGeoJSON).toBe(collection);
    expect(enabledFlagOf(viewportQueries.getWatersheds)).toBe(true);
  });

  it("does not render gauge points when watersheds is on and water is off, even if the streamflow cache still holds a gauge fetched earlier", () => {
    // Reproduces the concrete failure: react-query's `enabled: false` does not evict the
    // cache, so `streamflowQuery.data` can still hold the gauge array from when "water"
    // was previously on. Before the fix, LayerManager passed WaterLayer
    // `visible={waterEnabled || watershedsVisible}`, which read true here purely because
    // watersheds is on -- rendering gauge circles for measurements the user had
    // explicitly turned off, with no toggle claiming them.
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
    expect(waterProps?.watershedsVisible).toBe(true);
  });

  it("empties the watershed source rather than stranding polygons when the toggle is off", () => {
    viewportQueries.getWatersheds.mockReturnValue({ data: polygonCollection() });
    useMapStore.setState({ activeLayers: ["water"] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    const waterProps = lastRenderOf("WaterLayer");
    // Not null/undefined: WaterLayer only creates the source when this prop is a
    // collection, and only a collection can be set back to empty once it exists.
    const watersheds = waterProps?.watershedsGeoJSON as GeoJSON.FeatureCollection;
    expect(watersheds.type).toBe("FeatureCollection");
    expect(watersheds.features).toHaveLength(0);
    expect(enabledFlagOf(viewportQueries.getWatersheds)).toBe(false);
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

  it("neither queries nor draws the polygon feeds while both toggles are off", () => {
    useMapStore.setState({ activeLayers: [] });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(enabledFlagOf(viewportQueries.getWatersheds)).toBe(false);
    expect(enabledFlagOf(viewportQueries.getSoilSurvey)).toBe(false);
    expect(lastRenderOf("WaterLayer")?.visible).toBe(false);
    expect(lastRenderOf("SoilSurveyLayer")?.visible).toBe(false);
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
  /** The four procedures whose input carries `date` alongside a bbox. */
  const DATED_BBOX_QUERIES = [
    ["getStreamflow", viewportQueries.getStreamflow],
    ["getGroundwater", viewportQueries.getGroundwater],
    ["getVegetationIndex", viewportQueries.getVegetationIndex],
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
});
