import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  useDebouncedLayerDay,
  useLayerDay,
  useLayerRenderState,
  useLayerToggle,
  useLayerVisibility,
  useToggleLayer,
  useVegetationDisplayMode,
  useViewedLayerDays,
} from "@/lib/map/layer-toggle-context";
import { DEFAULT_VIEWPORT, useMapStore } from "@/stores/map-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { SCRUB_SETTLE_MS } from "@/stores/useMetricAtDate";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

const SERVER_CURRENT_DATE = "2026-08-04";

/**
 * Three layers whose records END on three different days, which is the whole point of the
 * feature: with one shared day only the deepest layer rendered anything, and per-layer dates
 * exist so each one opens on its own newest day instead of on a hole.
 */
const waterLayerCapability: SliderLayerCapability = {
  layerName: "water-gauges",
  temporalKind: "daily_series",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2026-05-24",
  latestObservedDate: "2026-08-02",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const vegetationLayerCapability: SliderLayerCapability = {
  layerName: "vegetation",
  temporalKind: "daily_series",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2022-08-05",
  latestObservedDate: "2026-06-14",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const fireLayerCapability: SliderLayerCapability = {
  layerName: "fire-detections",
  temporalKind: "event",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2026-01-02",
  latestObservedDate: "2026-08-04",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const capabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [waterLayerCapability, vegetationLayerCapability, fireLayerCapability],
};

function resetStores() {
  useMapStore.setState({
    viewport: { ...DEFAULT_VIEWPORT },
    activeLayers: ["fire", "water", "weather"],
    selectedFeatureId: null,
    is3DEnabled: false,
    isGlobeView: false,
    terrainExaggeration: 1.5,
    currentStyle: "dark",
    isTerrainEnabled: false,
  });
  useTimeSliderStore.setState({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,
  });
}

describe("layer toggle context", () => {
  beforeEach(() => {
    resetStores();
  });

  it("toggling a layer flips what should render, and nothing else's visibility", () => {
    const { result } = renderHook(() => ({
      visibility: useLayerVisibility(),
      toggle: useToggleLayer(),
    }));

    expect(result.current.visibility.water).toBe(true);
    expect(result.current.visibility.vegetation).toBe(false);

    act(() => {
      result.current.toggle("water");
    });

    expect(result.current.visibility.water).toBe(false);
    // Toggling one layer must not disturb an unrelated one.
    expect(result.current.visibility.fire).toBe(true);
  });

  it("keeps a withheld layer off even if forced into activeLayers", () => {
    // `soil` is the currently withheld entry: getEnvironmentalTileTemplate returns an empty
    // string until a first-party SoilGrids raster release exists. (This was `demand-heatmap`
    // until the k-anonymity floor satisfied its gate, and `building-footprints` until that
    // layer was removed outright on 2026-08-15.)
    useMapStore.setState({ activeLayers: ["soil"] });
    const { result } = renderHook(() => useLayerVisibility());
    expect(result.current["soil"]).toBe(false);
  });

  it("toggling a layer on changes what its own slider requests, and off withdraws the request", () => {
    useTimeSliderStore.setState({
      layerDates: { water: "2026-08-03" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    useMapStore.setState({ activeLayers: [] });

    const { result } = renderHook(() => ({
      water: useLayerRenderState("water"),
      toggle: useToggleLayer(),
    }));

    expect(result.current.water.isToggledOn).toBe(false);
    expect(result.current.water.shouldRender).toBe(false);

    act(() => {
      result.current.toggle("water");
    });

    expect(result.current.water.isToggledOn).toBe(true);
    expect(result.current.water.shouldRender).toBe(true);
    // "What the slider requests" for this layer: the day and variant it would ask
    // getMetricAtDate for, carried through without the caller wiring anything in.
    expect(result.current.water.selectedDate).toBe("2026-08-03");
    expect(result.current.water.variant).toBe("observed");
    expect(result.current.water.availability).toBe("published");
  });

  it("does not duplicate or desync from useMapStore: both read the same switch", () => {
    const { result } = renderHook(() => ({
      contextView: useLayerToggle("water"),
      storeView: useMapStore((state) => state.activeLayers.includes("water")),
    }));

    expect(result.current.contextView).toBe(true);
    expect(result.current.storeView).toBe(true);

    // Mutate through the store's own action, bypassing the context entirely.
    act(() => {
      useMapStore.getState().toggleLayer("water");
    });

    expect(result.current.contextView).toBe(false);
    expect(result.current.storeView).toBe(false);
    expect(result.current.contextView).toBe(result.current.storeView);
  });
});

/**
 * Each layer's own day. The map is a mixed-time composite now -- fire on one day beside
 * vegetation on another -- so the two properties worth defending are that every layer opens on
 * ITS OWN newest published day rather than on a shared one, and that moving one row's slider
 * moves exactly that row.
 */
describe("useLayerDay", () => {
  beforeEach(() => {
    resetStores();
  });

  it("opens every layer on its own latest observed day, not on a day they share", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });

    const { result } = renderHook(() => ({
      water: useLayerDay("water"),
      vegetation: useLayerDay("vegetation"),
      fire: useLayerDay("fire"),
    }));

    expect(result.current.water.selectedDate).toBe("2026-08-02");
    expect(result.current.vegetation.selectedDate).toBe("2026-06-14");
    expect(result.current.fire.selectedDate).toBe("2026-08-04");
    // Nothing is stored: these are three defaults, not three selections.
    expect(useTimeSliderStore.getState().layerDates).toEqual({});
  });

  it("marks a layer behind its own record and never marks one whose record is unknown", () => {
    useTimeSliderStore.setState({
      layerDates: { vegetation: "2025-06-14" },
      forecastVariant: "monte_carlo",
      capabilities,
    });

    const { result } = renderHook(() => ({
      vegetation: useLayerDay("vegetation"),
      fire: useLayerDay("fire"),
      drought: useLayerDay("drought"),
    }));

    expect(result.current.vegetation.isBehindLatestObservedDate).toBe(true);
    expect(result.current.vegetation.latestObservedDate).toBe("2026-06-14");
    // On its own latest, so it is not behind anything.
    expect(result.current.fire.isBehindLatestObservedDate).toBe(false);
    // `drought` has no geo.layers row, so nothing measured says it is stale. Marking it would
    // assert a staleness the client cannot see.
    expect(result.current.drought.latestObservedDate).toBeNull();
    expect(result.current.drought.isBehindLatestObservedDate).toBe(false);
    expect(result.current.drought.selectedDate).toBe(SERVER_CURRENT_DATE);
  });

  it("moves one layer's day without touching any other layer's", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });

    const { result } = renderHook(() => ({
      water: useLayerDay("water"),
      vegetation: useLayerDay("vegetation"),
    }));

    act(() => {
      useTimeSliderStore.getState().setLayerDate("water", "2026-06-01");
    });

    expect(result.current.water.selectedDate).toBe("2026-06-01");
    // Unchanged, and still its own default rather than water's new day.
    expect(result.current.vegetation.selectedDate).toBe("2026-06-14");
  });

  it("follows a layer's newest day as later payloads land, until someone pins it", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useLayerDay("vegetation"));
    expect(result.current.selectedDate).toBe("2026-06-14");

    act(() => {
      useTimeSliderStore.getState().setCapabilities({
        ...capabilities,
        layers: [
          waterLayerCapability,
          { ...vegetationLayerCapability, latestObservedDate: "2026-06-22" },
          fireLayerCapability,
        ],
      });
    });
    // The default has to track the live edge; a copy taken when capabilities first arrived
    // would have pinned this layer to 2026-06-14 with nobody having touched its slider.
    expect(result.current.selectedDate).toBe("2026-06-22");

    act(() => {
      useTimeSliderStore.getState().setLayerDate("vegetation", "2026-06-01");
      useTimeSliderStore.getState().setCapabilities({
        ...capabilities,
        layers: [
          waterLayerCapability,
          { ...vegetationLayerCapability, latestObservedDate: "2026-06-30" },
          fireLayerCapability,
        ],
      });
    });
    // Pinned now, so a newer payload must NOT drag the layer off the day the user chose.
    expect(result.current.selectedDate).toBe("2026-06-01");
  });

  it("names no day for any layer before capabilities arrive", () => {
    const { result } = renderHook(() => useLayerDay("water"));

    // Not a browser-clock guess: without capabilities nothing knows which day is today, which
    // is the timezone disagreement serverCurrentDate exists to prevent.
    expect(result.current.selectedDate).toBeNull();
    expect(result.current.serverCurrentDate).toBeNull();
    expect(result.current.latestObservedDate).toBeNull();
  });
});

/**
 * The day one layer's warehouse-backed queries key on. Three properties are load-bearing and
 * none is cosmetic: the day must settle before it reaches a query (a day-granular scrub writes
 * on every pointer tick, and the map has ~8 layer children behind this), the server's today
 * must reach the query as NO date so the hot path keeps the one cache entry it has always had,
 * and the settle must be PER LAYER -- scrubbing fire must not re-render vegetation.
 */
describe("useDebouncedLayerDay", () => {
  beforeEach(() => {
    resetStores();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends no date before capabilities name a today", () => {
    const { result } = renderHook(() => useDebouncedLayerDay("water"));

    expect(result.current.settledDate).toBeNull();
    expect(result.current.serverCurrentDate).toBeNull();
    expect(result.current.requestDate).toBeUndefined();
  });

  it("sends no date at the server's today and sends one on any other day", () => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => ({
      fire: useDebouncedLayerDay("fire"),
      water: useDebouncedLayerDay("water"),
    }));

    // fire's latest IS the server's today, so the server's default answer is the right one and
    // spelling the date out would only mint a second cache entry for it.
    expect(result.current.fire.settledDate).toBe(SERVER_CURRENT_DATE);
    expect(result.current.fire.isOffServerToday).toBe(false);
    expect(result.current.fire.requestDate).toBeUndefined();
    // water's latest is two days back, so its day has to ride along -- which is now the common
    // case rather than the exception.
    expect(result.current.water.settledDate).toBe("2026-08-02");
    expect(result.current.water.requestDate).toBe("2026-08-02");
  });

  it("settles on the last day of a scrub rather than reporting every tick of it", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useDebouncedLayerDay("water"));

    act(() => {
      useTimeSliderStore.getState().setLayerDate("water", "2026-08-01");
      useTimeSliderStore.getState().setLayerDate("water", "2026-07-31");
      useTimeSliderStore.getState().setLayerDate("water", "2026-07-30");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS - 1);
    });

    // Mid-scrub: the day the queries key on has not moved, so no request was issued for the
    // two days the pointer merely passed over.
    expect(result.current.settledDate).toBe("2026-08-02");

    act(() => {
      vi.advanceTimersByTime(1);
    });

    expect(result.current.settledDate).toBe("2026-07-30");
    expect(result.current.requestDate).toBe("2026-07-30");
  });

  it("settles each layer on its own scrub, so one row's pointer cannot move another's", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => ({
      water: useDebouncedLayerDay("water"),
      vegetation: useDebouncedLayerDay("vegetation"),
    }));

    act(() => {
      useTimeSliderStore.getState().setLayerDate("water", "2026-07-30");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    expect(result.current.water.settledDate).toBe("2026-07-30");
    expect(result.current.vegetation.settledDate).toBe("2026-06-14");
    expect(result.current.vegetation.requestDate).toBe("2026-06-14");
  });

  it("does not re-render a layer while a different layer is being scrubbed", () => {
    // The reason `useSettledLayerDate` compares this layer's resolved day inside the
    // subscription instead of arming a timer on every store write: `LayerManager` sits above ~8
    // layer children, and a shared settle made every one of them re-render on every pointer
    // tick of any one row's scrub.
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    let vegetationRenderCount = 0;
    const { result } = renderHook(() => {
      vegetationRenderCount += 1;
      return useDebouncedLayerDay("vegetation");
    });
    const rendersAfterMount = vegetationRenderCount;

    act(() => {
      for (const day of ["2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29"]) {
        useTimeSliderStore.getState().setLayerDate("water", day);
      }
      vi.advanceTimersByTime(SCRUB_SETTLE_MS * 2);
    });

    expect(vegetationRenderCount).toBe(rendersAfterMount);
    expect(result.current.settledDate).toBe("2026-06-14");
  });

  it("returns to a dateless request when a scrub settles back on the server's today", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: { fire: "2026-07-30" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useDebouncedLayerDay("fire"));
    expect(result.current.requestDate).toBe("2026-07-30");

    act(() => {
      useTimeSliderStore.getState().setLayerDate("fire", SERVER_CURRENT_DATE);
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    expect(result.current.requestDate).toBeUndefined();
  });

  it("adopts a layer's own day immediately when the hook is pointed at a different layer", () => {
    // Waiting out a settle window here would report the PREVIOUS layer's day for a layer whose
    // slider nobody touched -- the worst possible reading on a mixed-time map.
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });
    interface WatchedLayer {
      layerId: "water" | "vegetation";
    }
    const { result, rerender } = renderHook(
      ({ layerId }: WatchedLayer) => useDebouncedLayerDay(layerId),
      { initialProps: { layerId: "water" } as WatchedLayer }
    );
    expect(result.current.settledDate).toBe("2026-08-02");

    act(() => {
      rerender({ layerId: "vegetation" });
    });

    expect(result.current.settledDate).toBe("2026-06-14");
  });
});

/**
 * What the agent and any mixed-date banner are told. Fire on one day beside vegetation on
 * another looks like one moment in a screenshot, so anything that describes the screen has to
 * be handed each layer's own day rather than allowed to assume a shared one.
 */
describe("useViewedLayerDays", () => {
  beforeEach(() => {
    resetStores();
  });

  it("reports only visible layers, each on its own day", () => {
    useMapStore.setState({ activeLayers: ["water", "vegetation"] });
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities,
    });

    const { result } = renderHook(() => useViewedLayerDays());

    expect(result.current).toEqual([
      {
        layerId: "water",
        warehouseLayerName: "water-gauges",
        date: "2026-08-02",
        isOnLatest: true,
      },
      {
        layerId: "vegetation",
        warehouseLayerName: "vegetation",
        date: "2026-06-14",
        isOnLatest: true,
      },
    ]);
    // `fire` is switched off, so it is not on screen and must not be described as if it were.
    expect(result.current.some((viewed) => viewed.layerId === "fire")).toBe(false);
  });

  it("flags only a layer provably behind its own newest day", () => {
    useMapStore.setState({ activeLayers: ["water", "weather"] });
    useTimeSliderStore.setState({
      layerDates: { water: "2026-07-20" },
      forecastVariant: "monte_carlo",
      capabilities,
    });

    const { result } = renderHook(() => useViewedLayerDays());
    const byLayer = new Map(result.current.map((viewed) => [viewed.layerId, viewed]));

    expect(byLayer.get("water")).toEqual({
      layerId: "water",
      warehouseLayerName: "water-gauges",
      date: "2026-07-20",
      isOnLatest: false,
    });
    // `weather-observations` is absent from this payload, so its newest day is unknown and it
    // falls back to the server's today. Unknown is not stale, and must not be reported as stale.
    expect(byLayer.get("weather")).toEqual({
      layerId: "weather",
      warehouseLayerName: "weather-observations",
      date: SERVER_CURRENT_DATE,
      isOnLatest: true,
    });
  });

  it("omits a layer whose day cannot be named rather than reporting a sentinel", () => {
    useMapStore.setState({ activeLayers: ["water", "vegetation"] });
    const { result } = renderHook(() => useViewedLayerDays());

    // No capabilities at all: there is no day to report for anything, and "uninitialized" is
    // not a day anyone is looking at.
    expect(result.current).toEqual([]);
  });
});

/**
 * The vegetation raster's year and month. These were vegetation-store state behind two panel
 * sliders of their own until 2026-08-05 -- a second, disagreeing clock. They are now a
 * projection of the VEGETATION row's day, and the two properties worth defending are that the
 * projection tracks that day at all, and that it does NOT move within a month: GIBS bins this
 * product monthly, so a day-granular scrub must not re-request a month-granular tile.
 */
describe("useVegetationDisplayMode", () => {
  beforeEach(() => {
    resetStores();
    // No `opacity` here: it moved out of this store to `layer-store.layerOpacity`, keyed per
    // LayerToggleId, so the vegetation raster no longer shares a scalar with anything.
    useVegetationStore.setState({
      mode: "ndvi",
      ndviMode: "absolute",
      showNDWI: false,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("has no composite period before capabilities name a day", () => {
    const { result } = renderHook(() => useVegetationDisplayMode());

    // Not a browser-clock guess: with no day there is honestly no period, and the layer
    // attaches no raster rather than drawing a month nobody selected.
    expect(result.current.year).toBeNull();
    expect(result.current.month).toBeNull();
    expect(result.current.compositePeriod).toBeNull();
    expect(result.current.compositeUnavailableReason).toBeNull();
  });

  it("projects the vegetation row's own day onto the composite month it falls in", () => {
    useTimeSliderStore.setState({
      layerDates: { vegetation: SERVER_CURRENT_DATE },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    expect(result.current.year).toBe(2026);
    expect(result.current.month).toBe(8);
    expect(result.current.compositePeriod).toBe("Aug 2026");
    expect(result.current.compositeUnavailableReason).toBeNull();
  });

  it("ignores another layer's day entirely", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: { vegetation: "2026-08-01" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    act(() => {
      // Scrubbing WATER back four months must not move the raster the vegetation row draws.
      useTimeSliderStore.getState().setLayerDate("water", "2026-06-05");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    expect(result.current.compositePeriod).toBe("Aug 2026");
  });

  it("holds the same period -- the same object -- across a scrub inside one month", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: { vegetation: "2026-08-01" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());
    const beforeScrub = result.current;

    act(() => {
      useTimeSliderStore.getState().setLayerDate("vegetation", "2026-08-03");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    // Referentially identical, not merely equal: VegetationLayer keys its setTiles effect on
    // these props, so a new object for the same month would re-request the same tiles.
    expect(result.current).toBe(beforeScrub);
    expect(result.current.compositePeriod).toBe("Aug 2026");
  });

  it("moves to the next composite when the scrub crosses a month boundary", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      layerDates: { vegetation: "2026-08-01" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    act(() => {
      useTimeSliderStore.getState().setLayerDate("vegetation", "2026-07-28");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    expect(result.current.month).toBe(7);
    expect(result.current.compositePeriod).toBe("Jul 2026");
  });

  it("names the reason when the selected day is outside what GIBS publishes", () => {
    // Before the product's first published composite (2025-02) -- these dates 404 upstream
    // rather than returning a blank tile, so the gap has to be stated, not drawn.
    useTimeSliderStore.setState({
      layerDates: { vegetation: "2024-07-15" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    expect(result.current.compositePeriod).toBe("Jul 2024");
    expect(result.current.compositeUnavailableReason).toContain("Jul 2024");
    expect(result.current.compositeUnavailableReason).toContain("published extent");
  });

  it("reports a future month as uncovered against the server's today, not the browser's", () => {
    // A month past the payload's today has no composite yet. Judged against
    // serverCurrentDate, so a machine running in a different year cannot flip this.
    useTimeSliderStore.setState({
      layerDates: { vegetation: "2026-09-10" },
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    expect(result.current.compositePeriod).toBe("Sep 2026");
    expect(result.current.compositeUnavailableReason).toContain("Sep 2026");
  });
});
