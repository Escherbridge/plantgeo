import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  useDebouncedMapDay,
  useLayerRenderState,
  useLayerToggle,
  useLayerVisibility,
  useMapDay,
  useToggleLayer,
  useVegetationDisplayMode,
} from "@/lib/map/layer-toggle-context";
import { DEFAULT_VIEWPORT, useMapStore } from "@/stores/map-store";
import { UNINITIALIZED_DATE, useTimeSliderStore } from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { SCRUB_SETTLE_MS } from "@/stores/useMetricAtDate";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

const SERVER_CURRENT_DATE = "2026-08-04";

const waterLayerCapability: SliderLayerCapability = {
  layerName: "water-gauges",
  temporalKind: "daily_series",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2026-05-24",
};

const capabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  layers: [waterLayerCapability],
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
    selectedDate: UNINITIALIZED_DATE,
    forecastVariant: "monte_carlo",
    capabilities: null,
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
    // `building-footprints` is the currently withheld entry: its tile function is live but
    // geo.osm_buildings has no rows. (This was `demand-heatmap` until the k-anonymity floor
    // satisfied its gate and the registry opened it.)
    useMapStore.setState({ activeLayers: ["building-footprints"] });
    const { result } = renderHook(() => useLayerVisibility());
    expect(result.current["building-footprints"]).toBe(false);
  });

  it("toggling a layer on changes what the slider requests for it, and off withdraws the request", () => {
    useTimeSliderStore.setState({
      selectedDate: "2026-08-03",
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

  it("carries the slider's selected date to a layer without prop drilling", () => {
    useTimeSliderStore.setState({
      selectedDate: "2026-06-01",
      forecastVariant: "ml",
      capabilities,
    });

    // useLayerRenderState takes only a toggle id -- no date prop is passed in -- yet it
    // reflects the ambient slider selection, and useMapDay agrees with it independently.
    const { result } = renderHook(() => ({
      day: useMapDay(),
      water: useLayerRenderState("water"),
    }));

    expect(result.current.day.selectedDate).toBe("2026-06-01");
    expect(result.current.water.selectedDate).toBe("2026-06-01");
    expect(result.current.day.isOffServerToday).toBe(true);

    act(() => {
      useTimeSliderStore.getState().setSelectedDate(SERVER_CURRENT_DATE);
    });

    expect(result.current.day.selectedDate).toBe(SERVER_CURRENT_DATE);
    expect(result.current.water.selectedDate).toBe(SERVER_CURRENT_DATE);
    expect(result.current.day.isOffServerToday).toBe(false);
  });
});

/**
 * The day a warehouse-backed query keys on. Two properties are load-bearing and neither is
 * cosmetic: the day must settle before it reaches a query (a day-granular scrub writes on
 * every pointer tick, and the map has ~8 layer children behind this), and the server's today
 * must reach the query as NO date, so the hot path keeps the one cache entry it has always
 * had instead of double-fetching first paint under two keys.
 */
describe("useDebouncedMapDay", () => {
  beforeEach(() => {
    resetStores();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends no date before capabilities name a today", () => {
    // UNINITIALIZED_DATE is not a calendar date, and without capabilities nothing knows which
    // day is today -- guessing from the browser clock is the disagreement serverCurrentDate
    // exists to prevent.
    const { result } = renderHook(() => useDebouncedMapDay());

    expect(result.current.settledDate).toBeNull();
    expect(result.current.serverCurrentDate).toBeNull();
    expect(result.current.requestDate).toBeUndefined();
  });

  it("sends no date at the server's today", () => {
    useTimeSliderStore.setState({
      selectedDate: SERVER_CURRENT_DATE,
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useDebouncedMapDay());

    expect(result.current.settledDate).toBe(SERVER_CURRENT_DATE);
    expect(result.current.isOffServerToday).toBe(false);
    expect(result.current.requestDate).toBeUndefined();
  });

  it("settles on the last day of a scrub rather than reporting every tick of it", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      selectedDate: SERVER_CURRENT_DATE,
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useDebouncedMapDay());

    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2026-08-01");
      useTimeSliderStore.getState().setSelectedDate("2026-07-31");
      useTimeSliderStore.getState().setSelectedDate("2026-07-30");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS - 1);
    });

    // Mid-scrub: the day the queries key on has not moved, so no request was issued for the
    // two days the pointer merely passed over.
    expect(result.current.requestDate).toBeUndefined();
    expect(result.current.settledDate).toBe(SERVER_CURRENT_DATE);

    act(() => {
      vi.advanceTimersByTime(1);
    });

    expect(result.current.settledDate).toBe("2026-07-30");
    expect(result.current.isOffServerToday).toBe(true);
    expect(result.current.requestDate).toBe("2026-07-30");
  });

  it("returns to a dateless request when the scrub settles back on today", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      selectedDate: "2026-07-30",
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useDebouncedMapDay());
    expect(result.current.requestDate).toBe("2026-07-30");

    act(() => {
      useTimeSliderStore.getState().setSelectedDate(SERVER_CURRENT_DATE);
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    expect(result.current.requestDate).toBeUndefined();
  });
});

/**
 * The vegetation raster's year and month. These were vegetation-store state behind two panel
 * sliders of their own until 2026-08-05 -- a second, disagreeing clock. They are now a
 * projection of the one slider day, and the two properties worth defending are that the
 * projection tracks the day at all, and that it does NOT move within a month: GIBS bins this
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

  it("projects the slider's day onto the composite month it falls in", () => {
    useTimeSliderStore.setState({
      selectedDate: SERVER_CURRENT_DATE,
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    expect(result.current.year).toBe(2026);
    expect(result.current.month).toBe(8);
    expect(result.current.compositePeriod).toBe("Aug 2026");
    expect(result.current.compositeUnavailableReason).toBeNull();
  });

  it("holds the same period -- the same object -- across a scrub inside one month", () => {
    vi.useFakeTimers();
    useTimeSliderStore.setState({
      selectedDate: "2026-08-01",
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());
    const beforeScrub = result.current;

    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2026-08-20");
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
      selectedDate: "2026-08-01",
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2026-07-28");
      vi.advanceTimersByTime(SCRUB_SETTLE_MS);
    });

    expect(result.current.month).toBe(7);
    expect(result.current.compositePeriod).toBe("Jul 2026");
  });

  it("names the reason when the selected day is outside what GIBS publishes", () => {
    // Before the product's first published composite (2025-02) -- these dates 404 upstream
    // rather than returning a blank tile, so the gap has to be stated, not drawn.
    useTimeSliderStore.setState({
      selectedDate: "2024-07-15",
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
      selectedDate: "2026-09-10",
      forecastVariant: "monte_carlo",
      capabilities,
    });
    const { result } = renderHook(() => useVegetationDisplayMode());

    expect(result.current.compositePeriod).toBe("Sep 2026");
    expect(result.current.compositeUnavailableReason).toContain("Sep 2026");
  });
});
