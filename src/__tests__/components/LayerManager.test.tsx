import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MapProvider } from "@/lib/map/map-context";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { LAYER_REGISTRY } from "@/lib/map/layer-registry";
import { DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS } from "@/lib/map/tile-layer-date-filter";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import { SLIDER_STREAM_LAYER_NAMES } from "@/types/time-slider";
import type {
  SliderCapabilities,
  SliderLayerCapability,
  TemporalKind,
} from "@/types/time-slider";
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

/**
 * `useFireData` is the one dated feed that is not a tRPC hook, so it cannot be observed through
 * the `trpc` mock below. It is a spy rather than a plain stub because the day it receives is
 * exactly what several cases here assert: fire detections take the `fire` ROW's day, and no
 * other layer's.
 */
const fireDataHook = vi.hoisted(() =>
  vi.fn((_visible: boolean, _date?: string) => ({
    data: { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection,
    count: 0,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }))
);

vi.mock("@/hooks/useFireData", () => ({ useFireData: fireDataHook }));

/** The `date` argument `useFireData` was last called with. */
function fireRequestDate(): string | undefined {
  return fireDataHook.mock.calls.at(-1)?.[1];
}

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
function soilFieldInputOf(
  measure: "moisture" | "temperature" | "vpd"
): Record<string, unknown> {
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

/**
 * One published layer, spelled once. Every field the contract requires is present, because a
 * missing `latestObservedDate` is not a smaller fixture -- it is the state where a layer follows
 * the server's today instead of its own record, which is precisely what these cases measure.
 *
 * `describedFromDay: null` says the coverage lists describe this layer's whole axis, which is
 * what every layer here needs: these cases are about which DAY each feed takes, and a boundary
 * would put them in the business of what is known about that day.
 */
function publishedLayer(
  layerName: string,
  latestObservedDate: string,
  earliestObservedDate = "2026-05-24",
  temporalKind: TemporalKind = "daily_series"
): SliderLayerCapability {
  return {
    layerName,
    temporalKind,
    forecastHorizonDays: 0,
    forecastVariants: [],
    earliestObservedDate,
    latestObservedDate,
    coverageGaps: [],
    thinRanges: [],
    describedFromDay: null,
  };
}

/**
 * Every warehouse-backed layer LayerManager reads a day for, each on a DIFFERENT newest day.
 *
 * Deliberately all different: with per-layer dates a fixture where two layers share a day cannot
 * distinguish "each feed took its own layer's date" from "one date was fanned across all of
 * them", which is the exact regression these cases exist to catch.
 *
 * Every layer here is `daily_series`, INCLUDING the three tile layers the read model really
 * classifies as snapshots. That is deliberate and is what keeps these cases about day-fanning
 * alone: a payload where every date-filterable toggle carries an axis is the one in which every
 * one of them must be filtered, so a fanned-out date has nowhere to hide. The snapshot half --
 * a layer with no selectable day getting no filter at all -- is a different property and has its
 * own fixture and its own describe block below.
 *
 * The five stream-backed toggles are absent on purpose: no capability for their stream means no
 * axis, so they exercise the fallback to the server's today.
 */
const sliderCapabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  layers: [
    // Its newest day IS the server's today, which is the one case that must send no date.
    publishedLayer("fire-detections", SERVER_CURRENT_DATE),
    publishedLayer("water-gauges", "2026-07-30"),
    publishedLayer("vegetation", "2026-06-14"),
    publishedLayer("weather-observations", "2026-08-02"),
    publishedLayer("fire-perimeters", "2026-07-21"),
    publishedLayer("evacuation-zones", "2026-07-22"),
    publishedLayer("burn-severity", "2026-07-23"),
    publishedLayer("sensors", "2026-07-24"),
  ],
};

/**
 * The same payload with the temporal kinds the read model actually publishes.
 *
 * `evacuation-zones` and `sensors` are declared `snapshot` in LAYER_TEMPORAL_KINDS, so
 * `sliderDomain` refuses them, those rows draw no slider, and the map must not date-filter what
 * the reader has no way to re-date.
 *
 * `burn-severity` is `event` and MUST be spelled that way here. It was previously absent from
 * LAYER_TEMPORAL_KINDS and inherited `snapshot`, and this fixture encoded that inheritance --
 * which is why the suite stayed green while every MTBS scar through 2026 drew beneath a
 * perimeter view scrubbed to 2024. A fixture that restates a registry's default rather than its
 * decision cannot catch a wrong default.
 */
const realTemporalKindCapabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 0,
  layers: [
    publishedLayer("fire-perimeters", "2026-07-21", "2026-05-24", "event"),
    publishedLayer("evacuation-zones", "2026-07-22", "2026-05-24", "snapshot"),
    publishedLayer("burn-severity", "2026-07-23", "2026-05-24", "event"),
    publishedLayer("sensors", "2026-07-24", "2026-05-24", "snapshot"),
  ],
};

/**
 * A payload that publishes the five streams that are not `geo.features` rows.
 *
 * They carried `warehouseLayerName: null` until 2026-08-09, which cost them their axis and left
 * every one of their readers pinned to the live edge even though all five accept a day.
 */
const streamBackedCapabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 0,
  layers: [
    publishedLayer(SLIDER_STREAM_LAYER_NAMES.drought, "2026-07-28", "2024-01-02"),
    publishedLayer(SLIDER_STREAM_LAYER_NAMES.soilMoisture, "2026-07-27", "2022-04-30"),
    publishedLayer(SLIDER_STREAM_LAYER_NAMES.soilTemperature, "2026-07-26", "2022-04-30"),
    publishedLayer(
      SLIDER_STREAM_LAYER_NAMES.soilVapourPressureDeficit,
      "2026-07-25",
      "2022-04-30"
    ),
    publishedLayer(SLIDER_STREAM_LAYER_NAMES.climateField, "2026-07-24", "2022-04-30"),
  ],
};

/** No per-layer override and no capabilities: what every case here but the date ones assumes. */
function resetSliderStore() {
  useTimeSliderStore.setState({
    layerDates: {},
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
  // useDebouncedLayerDay's subscription while the tree is still mounted, scheduling a settle
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
      viewport: {
        longitude: -116.2,
        latitude: 43.6,
        zoom: 6.5,
        bearing: 0,
        pitch: 0,
        widthPx: 1024,
        heightPx: 512,
      },
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
      viewport: {
        longitude: -116.2,
        latitude: 43.6,
        zoom: 5,
        bearing: 0,
        pitch: 0,
        widthPx: 1024,
        heightPx: 512,
      },
    });

    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    act(() => {
      useMapStore.setState({
        viewport: {
          longitude: -116.2,
          latitude: 43.6,
          zoom: 14,
          bearing: 0,
          pitch: 0,
          widthPx: 1024,
          heightPx: 512,
        },
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
 * Every warehouse-backed feed here used to be dateless, then took ONE map-wide day, and since
 * 2026-08-09 takes the day of the LAYER it draws. These cases pin all three properties that
 * makes true: each feed reads its own layer's day, the server's today is still sent as an
 * omission rather than as a date, and a day nobody can name is still not guessed at.
 *
 * The fixture gives every published layer a different newest day on purpose -- see
 * `sliderCapabilities`. A fixture where two layers agreed could not tell "took its own layer's
 * date" apart from "took one date fanned across all of them".
 */
describe("LayerManager gives each warehouse-backed feed its own layer's day", () => {
  /** Every procedure whose input carries `date` alongside a bbox, and the toggle it draws. */
  const DATED_BBOX_QUERIES = [
    ["getStreamflow", viewportQueries.getStreamflow],
    ["getGroundwater", viewportQueries.getGroundwater],
    ["getVegetationIndex", viewportQueries.getVegetationIndex],
    ["getSoilField", viewportQueries.getSoilField],
    ["getClimateField", viewportQueries.getClimateField],
    ["getWeatherForBbox", viewportQueries.getWeatherForBbox],
  ] as const;

  /** Renders with capabilities in place and, optionally, some rows scrubbed off their default. */
  function renderWithLayerDates(layerDates: Record<string, string> = {}) {
    useTimeSliderStore.setState({
      layerDates,
      forecastVariant: "monte_carlo",
      capabilities: sliderCapabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);
    return fakeMap;
  }

  /**
   * The whole feature, stated as one assertion. Before this, every layer shared "today" and only
   * vegetation had four years of depth, so most layers rendered empty; each now opens on its own
   * newest published day, which for these four are four different days.
   */
  it("opens every feed on its own layer's newest published day rather than on one shared date", () => {
    renderWithLayerDates();

    expect(
      (inputOf(viewportQueries.getStreamflow) as { date?: string }).date
    ).toBe("2026-07-30");
    expect(
      (inputOf(viewportQueries.getVegetationIndex) as { date?: string }).date
    ).toBe("2026-06-14");
    expect(
      (inputOf(viewportQueries.getWeatherForBbox) as { date?: string }).date
    ).toBe("2026-08-02");
    // Both water feeds are drawn by the one `water` toggle, so they share that row's day --
    // a consequence of there being one control over them, not of any map-wide date.
    expect(
      (inputOf(viewportQueries.getGroundwater) as { date?: string }).date
    ).toBe("2026-07-30");
  });

  /**
   * The independence property, and the one a map-wide day cannot have. Scrubbing water's row
   * must leave vegetation exactly where it was -- if it did not, every row would be a second
   * control over one value, which is the state this feature removes.
   */
  it("moves only the scrubbed layer's feed, leaving every other feed on its own day", () => {
    renderWithLayerDates({ water: "2026-07-01" });

    expect(
      (inputOf(viewportQueries.getStreamflow) as { date?: string }).date
    ).toBe("2026-07-01");
    expect(
      (inputOf(viewportQueries.getVegetationIndex) as { date?: string }).date
    ).toBe("2026-06-14");
    expect(
      (inputOf(viewportQueries.getWeatherForBbox) as { date?: string }).date
    ).toBe("2026-08-02");
  });

  it("omits the date for a layer sitting on the server's today, so that feed keeps one cache entry", () => {
    renderWithLayerDates();

    // `fire-detections` publishes up to the server's today, so its row opens there and the day
    // must ride along as an omission: the server treats an absent date and today's date
    // identically, and sending it would mint a second react-query entry for one answer.
    expect(fireRequestDate()).toBeUndefined();
    // This fixture publishes no capability for the `drought-areas` stream, so drought has no
    // newest day of its own and falls back to the server's today -- also dateless. tRPC keys
    // `undefined` input differently from an object, so this must stay literally undefined
    // rather than becoming `{ date: undefined }`.
    expect(inputOf(viewportQueries.getDroughtClassification)).toBeUndefined();
    // The three ERA5-Land fields and the NASA POWER field have no stream capability here
    // either, for the same reason.
    expect(
      (inputOf(viewportQueries.getSoilField) as { date?: string }).date
    ).toBeUndefined();
    expect(
      (inputOf(viewportQueries.getClimateField) as { date?: string }).date
    ).toBeUndefined();
  });

  /**
   * F4, stated as one case. These five toggles carried `warehouseLayerName: null`, so
   * `resolveLayerDate` found no `latestObservedDate` for them and fell through to the server's
   * today on every render -- five of roughly ten dated layers permanently pinned to the live
   * edge, with no slider on the row and no way back, even though `getDroughtClassification`,
   * `getSoilField` and `getClimateField` have all accepted a `date` throughout.
   *
   * Each stream is on a different day for the same reason every other fixture here is: it is
   * what tells "each row took its own stream's day" apart from "one day fanned across five".
   */
  it("opens each stream-backed feed on its own stream's newest day once the payload publishes it", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: streamBackedCapabilities,
    });
    useMapStore.setState({
      activeLayers: ["drought", "soil-moisture", "soil-temperature", "soil-vpd", "climate-field"],
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(inputOf(viewportQueries.getDroughtClassification)).toEqual({ date: "2026-07-28" });
    expect(soilFieldInputOf("moisture").date).toBe("2026-07-27");
    expect(soilFieldInputOf("temperature").date).toBe("2026-07-26");
    expect(soilFieldInputOf("vpd").date).toBe("2026-07-25");
    expect((inputOf(viewportQueries.getClimateField) as { date?: string }).date).toBe(
      "2026-07-24"
    );
  });

  /**
   * The property the pinning actually cost: these readers accepted a historical day before the
   * per-layer sliders landed and must accept one again. A day years behind the live edge is the
   * case that matters -- the ERA5-Land lattice goes back to 2022-04-30, and none of it was
   * reachable while the row had no axis to scrub.
   */
  it("carries a years-old day into a stream-backed feed whose row was scrubbed back", () => {
    useTimeSliderStore.setState({
      layerDates: {
        drought: "2024-06-01",
        "soil-moisture": "2023-02-15",
        "climate-field": "2023-11-30",
      },
      forecastVariant: "monte_carlo",
      capabilities: streamBackedCapabilities,
    });
    useMapStore.setState({
      activeLayers: ["drought", "soil-moisture", "climate-field"],
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(inputOf(viewportQueries.getDroughtClassification)).toEqual({ date: "2024-06-01" });
    expect(soilFieldInputOf("moisture").date).toBe("2023-02-15");
    expect((inputOf(viewportQueries.getClimateField) as { date?: string }).date).toBe(
      "2023-11-30"
    );
  });

  it("carries a past day into a feed whose row was scrubbed off today", () => {
    renderWithLayerDates({ fire: "2026-07-30", drought: "2026-07-29" });

    expect(fireRequestDate()).toBe("2026-07-30");
    expect(inputOf(viewportQueries.getDroughtClassification)).toEqual({
      date: "2026-07-29",
    });
  });

  it("stays dateless before capabilities name a today, rather than guessing from the browser clock", () => {
    // The default store state: no overrides and no capabilities.
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(fireRequestDate()).toBeUndefined();
    expect(inputOf(viewportQueries.getDroughtClassification)).toBeUndefined();
    for (const [name, query] of DATED_BBOX_QUERIES) {
      const input = inputOf(query) as { bbox?: string; date?: string };
      expect(input.bbox, name).toBeTypeOf("string");
      expect(input.date, name).toBeUndefined();
    }
  });

  /**
   * The GIBS raster is addressed by a month, so it takes year/month rather than a day. Those
   * came from two sliders of vegetation's own until 2026-08-05, then from the map-wide day;
   * they are the VEGETATION row's day projected onto a month now, and this is the seam where
   * that projection reaches the renderer.
   */
  it("gives the vegetation raster its own row's month, not another layer's and not a stored one", () => {
    renderWithLayerDates();

    // June, from vegetation's own 2026-06-14 -- not August, which is where the server's today
    // and half the other rows sit.
    const vegetationProps = lastRenderOf("VegetationLayer");
    expect(vegetationProps?.year).toBe(2026);
    expect(vegetationProps?.month).toBe(6);
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

/**
 * The style-baked Martin layers are not React-mounted, so they take their day as a MapLibre
 * filter rather than as a prop. They were filtered by one fanned-out date until 2026-08-09;
 * each is its own dock row with its own slider, so each must now be filtered on its own day.
 */
/** The last filter written for a style layer, or `undefined` when none was ever written. */
function lastFilterWriteFor(fakeMap: FakeMap, styleLayerId: string): unknown {
  const write = fakeMap.setFilter.mock.calls
    .filter(([writtenLayerId]) => writtenLayerId === styleLayerId)
    .at(-1);
  return write === undefined ? undefined : write[1];
}

/** True when the applier wrote this style layer at all, whatever it wrote. */
function wasWritten(fakeMap: FakeMap, styleLayerId: string): boolean {
  return fakeMap.setFilter.mock.calls.some(
    ([writtenLayerId]) => writtenLayerId === styleLayerId
  );
}

/** The day a filter expression was built from, or null when the layer was left unfiltered. */
function filteredDayFor(fakeMap: FakeMap, styleLayerId: string): string | null {
  const filter = lastFilterWriteFor(fakeMap, styleLayerId) as unknown[] | undefined;
  if (!Array.isArray(filter)) return null;
  // ["any", ["!", ["has", "observed_day"]], ["<=", ["get", "observed_day"], day]]
  const upperBound = filter.at(-1) as unknown[] | undefined;
  return typeof upperBound?.[2] === "string" ? upperBound[2] : null;
}

describe("LayerManager filters each style-baked tile layer on its own layer's day", () => {
  /**
   * Walks the exported constant rather than a list spelled here, so a fifth date-filterable
   * toggle added to `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS` without a matching
   * `useDebouncedLayerDay` call in LayerManager fails this case instead of silently drawing its
   * whole record at once.
   */
  it("filters every date-filterable tile layer on that layer's own day", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: sliderCapabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
      const warehouseLayerName = LAYER_REGISTRY[toggleId].warehouseLayerName;
      const expectedDay =
        sliderCapabilities.layers.find((layer) => layer.layerName === warehouseLayerName)
          ?.latestObservedDate ?? null;
      expect(expectedDay, `${toggleId} needs a capability in the fixture`).not.toBeNull();
      for (const styleLayerId of LAYER_REGISTRY[toggleId].styleLayerIds) {
        expect(filteredDayFor(fakeMap, styleLayerId), styleLayerId).toBe(expectedDay);
      }
    }
  });

  /**
   * The four days are four different days in the fixture, so this is what distinguishes "each
   * layer filtered on its own day" from "one day fanned across all four", which is what the
   * global slider did.
   */
  it("gives four date-filterable layers four different days rather than one fanned-out date", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: sliderCapabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    const days = DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS.map((toggleId: LayerToggleId) =>
      filteredDayFor(fakeMap, LAYER_REGISTRY[toggleId].styleLayerIds[0])
    );
    expect(new Set(days).size).toBe(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS.length);
  });

  /**
   * `requestDate` is undefined at the server's today so a query keys the same as a dateless one;
   * borrowing that here would drop the filter entirely and restore the pre-2026-08-08 behaviour
   * where every published row drew at every point on the axis. A style filter costs no request,
   * so the day is always applied.
   */
  it("still filters a tile layer whose row sits on the server's today", () => {
    useTimeSliderStore.setState({
      layerDates: { sensors: SERVER_CURRENT_DATE },
      forecastVariant: "monte_carlo",
      capabilities: sliderCapabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);

    expect(filteredDayFor(fakeMap, "sensors")).toBe(SERVER_CURRENT_DATE);
  });

  /**
   * A basemap swap rebuilds every style layer from its authored filter, and `style.load` can
   * fire synchronously with `isStyleLoaded()` still false -- so the direct re-application inside
   * that handler is the safety net, and it has to reach every layer's own day, not one.
   */
  it("re-applies every layer's own filter after a basemap swap", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: sliderCapabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);
    fakeMap.setFilter.mockClear();

    act(() => {
      fakeMap.setStyleLoaded(false);
      fakeMap.emit("style.load");
    });

    expect(filteredDayFor(fakeMap, "fire-perimeters")).toBe("2026-07-21");
    expect(filteredDayFor(fakeMap, "evacuation-zones")).toBe("2026-07-22");
    expect(filteredDayFor(fakeMap, "burn-severity")).toBe("2026-07-23");
    expect(filteredDayFor(fakeMap, "sensors")).toBe("2026-07-24");
  });
});

/**
 * A layer that is date-FILTERED on the map must have a control, and a layer with a control must
 * be date-filtered. Never one without the other -- `hasSelectableDay` is the single function
 * both halves ask, and these cases are what hold the two together.
 *
 * The bug they pin: `sliderDomain` refuses a snapshot, so evacuation-zones, sensors and
 * burn-severity draw no slider -- while LayerManager still resolved a day for each and installed
 * `["<=", ["get","observed_day"], thatDay]`. The day it installed was `latestObservedDate`,
 * which is by contract the newest day at or ABOVE the density floor, so through every
 * partially-ingested live-edge day -- the normal state of a running ingest lane -- every feature
 * observed that day was filtered off the map, with no slider on the row, no date printed and no
 * caption. Before per-layer dates the global day was `serverCurrentDate` and
 * `tileLayerDateFilter(null)` cleared the filter, so those features drew.
 */
describe("LayerManager keeps the date filter and the row's own control in step", () => {
  function renderWithCapabilities(capabilities: SliderCapabilities | null): FakeMap {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(true);
    renderLayerManager(fakeMap);
    return fakeMap;
  }

  /** The date-filterable toggles this payload gives no axis to, and so no control. */
  const SNAPSHOT_TILE_LAYER_IDS = ["evacuation-zones", "evacuation-zones-outline", "sensors"];

  /**
   * `burn-severity` sits on the OTHER side of this rule, and deliberately so.
   *
   * It is `event`: an MTBS scar is dated by the release that mapped it, so the layer accretes.
   * It was once absent from LAYER_TEMPORAL_KINDS, inherited `snapshot`, and therefore landed in
   * the list above -- self-consistently, since it had no control either, so this very test
   * passed while every scar through 2026 drew beneath a perimeter view scrubbed to 2024. It is
   * named here rather than merely deleted from that list so the two halves stay opposed.
   */
  const EVENT_TILE_LAYER_IDS = ["burn-severity", "burn-severity-outline"];

  it("installs no date filter on a snapshot tile layer, which has no slider to change one with", () => {
    const fakeMap = renderWithCapabilities(realTemporalKindCapabilities);

    for (const styleLayerId of SNAPSHOT_TILE_LAYER_IDS) {
      expect(lastFilterWriteFor(fakeMap, styleLayerId), styleLayerId).toBeUndefined();
      // Written, not merely left alone: the applier must be the thing that decided this.
      expect(wasWritten(fakeMap, styleLayerId), styleLayerId).toBe(true);
    }
    // The complement, asserted in the same breath: an accreting layer MUST be bounded, or it
    // draws its whole record under a row scrubbed into the past.
    for (const styleLayerId of EVENT_TILE_LAYER_IDS) {
      expect(lastFilterWriteFor(fakeMap, styleLayerId), styleLayerId).toBeDefined();
    }
  });

  it("still filters the one date-filterable layer that does have an axis", () => {
    const fakeMap = renderWithCapabilities(realTemporalKindCapabilities);

    // `fire-perimeters` is an `event` layer, so it keeps its slider and keeps its filter. The
    // fix is not "stop filtering tile layers" -- it is "filter exactly the ones a reader can
    // re-date".
    expect(filteredDayFor(fakeMap, "fire-perimeters")).toBe("2026-07-21");
    expect(filteredDayFor(fakeMap, "fire-perimeters-outline")).toBe("2026-07-21");
  });

  /**
   * The concrete failure, in the terms it actually appears in. `latestObservedDate` is the
   * newest DENSE day; the newest RECORDED day can be later, and during an ingest it usually is.
   * An upper bound of the dense day hides every feature observed on the day currently landing.
   */
  it("leaves a partially-ingested live-edge day drawable by installing no upper bound at all", () => {
    const partiallyIngested: SliderCapabilities = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      futureAxisDays: 0,
      layers: [
        // Dense through 2026-07-24 while rows for 2026-08-04 are still landing.
        publishedLayer("sensors", "2026-07-24", "2026-05-24", "snapshot"),
      ],
    };
    const fakeMap = renderWithCapabilities(partiallyIngested);

    const filter = lastFilterWriteFor(fakeMap, "sensors");
    expect(filter).toBeUndefined();
    // Said the other way round, because this is the assertion that would have failed before:
    // no expression on this layer names the dense day.
    expect(JSON.stringify(filter ?? null)).not.toContain("2026-07-24");
  });

  it("clears a filter the previous style carried when the layer loses its selectable day", () => {
    // Starts with an axis for every tile toggle, so all four are filtered...
    const fakeMap = renderWithCapabilities(sliderCapabilities);
    expect(filteredDayFor(fakeMap, "sensors")).toBe("2026-07-24");

    // ...then a later payload reclassifies them, as the read model really does.
    act(() => {
      useTimeSliderStore.getState().setCapabilities(realTemporalKindCapabilities);
    });

    expect(lastFilterWriteFor(fakeMap, "sensors")).toBeUndefined();
  });

  it("re-clears rather than re-filters a snapshot layer after a basemap swap", () => {
    const fakeMap = renderWithCapabilities(realTemporalKindCapabilities);
    fakeMap.setFilter.mockClear();

    act(() => {
      fakeMap.setStyleLoaded(false);
      fakeMap.emit("style.load");
    });

    // The style.load safety net rebuilds every layer's filter from the ref. A snapshot layer's
    // correct filter is no filter, and "no filter" has to be WRITTEN here: the rebuilt style
    // could otherwise be left carrying whatever the previous pass installed.
    expect(wasWritten(fakeMap, "sensors")).toBe(true);
    expect(lastFilterWriteFor(fakeMap, "sensors")).toBeUndefined();
    expect(filteredDayFor(fakeMap, "fire-perimeters")).toBe("2026-07-21");
  });

  it("installs no filter on any tile layer while the capabilities payload is still loading", () => {
    // No capabilities: nothing knows any layer's axis, so nothing may claim a day for one.
    const fakeMap = renderWithCapabilities(null);

    for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
      for (const styleLayerId of LAYER_REGISTRY[toggleId].styleLayerIds) {
        expect(lastFilterWriteFor(fakeMap, styleLayerId), styleLayerId).toBeUndefined();
      }
    }
  });
});
