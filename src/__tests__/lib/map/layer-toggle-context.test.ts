import { beforeEach, describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  useLayerRenderState,
  useLayerToggle,
  useLayerVisibility,
  useMapDay,
  useToggleLayer,
} from "@/lib/map/layer-toggle-context";
import { DEFAULT_VIEWPORT, useMapStore } from "@/stores/map-store";
import { UNINITIALIZED_DATE, useTimeSliderStore } from "@/stores/time-slider-store";
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

  it("keeps a governance-withheld layer off even if forced into activeLayers", () => {
    useMapStore.setState({ activeLayers: ["demand-heatmap"] });
    const { result } = renderHook(() => useLayerVisibility());
    expect(result.current["demand-heatmap"]).toBe(false);
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
