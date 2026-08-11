import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { useMapStore } from "@/stores/map-store";
import { useLayerStore } from "@/stores/layer-store";
import { useClimateStore } from "@/stores/climate-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import {
  climateFieldStreamName,
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

/**
 * The nine NASA POWER rows read and draw independently.
 *
 * This is the property the split was for: until 2026-08-10 one `climate-field` toggle drew one
 * signal at a time on one day, and the axis behind it was computed over every signal in the
 * lane unioned together. These cases assert the two halves that replaced it -- each signal
 * reads on ITS OWN row's day, and each is drawn in a form that signal actually permits.
 *
 * The leaf `ClimateFieldLayer` is stubbed to nothing: it is the only part that talks to
 * MapLibre, and what is under test here is which day, which signal and which form reach the
 * query -- not addSource/addLayer, which `ClimateFieldLayer`'s own coverage owns.
 */
// Both parameters are declared, unlike the equivalent stub in LayerManager.test.tsx: these
// cases read the query INPUT and the `enabled` flag off `mock.calls`, and a zero-arity stub
// types that tuple as empty.
const climateQuery = vi.hoisted(() => ({
  useQuery: vi.fn((_input: unknown, _options: unknown) => ({ data: undefined })),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: { environmental: { getClimateField: { useQuery: climateQuery.useQuery } } },
}));

const drawnLayers = vi.hoisted(() => ({
  renders: [] as Record<string, unknown>[],
}));

vi.mock("@/components/map/layers/ClimateFieldLayer", () => ({
  ClimateFieldLayer: (props: Record<string, unknown>) => {
    drawnLayers.renders.push(props);
    return null;
  },
}));

import { ClimateFieldLayers } from "@/components/map/layers/ClimateFieldLayers";

const SERVER_CURRENT_DATE = "2026-08-10";
const BBOX = "-125,42,-116,49";

/** A published stream with a dense axis, so `resolveLayerDate` opens on `latest`. */
function publishedStream(layerName: string, latest: string): SliderLayerCapability {
  return {
    layerName,
    temporalKind: "daily_series",
    forecastHorizonDays: 0,
    forecastVariants: [],
    earliestObservedDate: "2022-04-30",
    latestObservedDate: latest,
    coverageGaps: [],
    thinRanges: [],
    describedFromDay: "2022-04-30",
  };
}

/**
 * Every signal on a DIFFERENT latest day. That is what tells "each row took its own stream's
 * day" apart from "one day fanned across nine" -- with a shared fixture day both pass.
 */
function capabilitiesWithDistinctDays(): SliderCapabilities {
  return {
    serverCurrentDate: SERVER_CURRENT_DATE,
    futureAxisDays: 0,
    layers: CLIMATE_FIELD_SIGNAL_IDS.map((signal, index) =>
      publishedStream(
        climateFieldStreamName(signal),
        `2026-07-${String(10 + index).padStart(2, "0")}`
      )
    ),
  };
}

/** The query input recorded for one signal, or undefined if it was never asked for. */
function inputFor(signal: ClimateFieldSignalId) {
  for (const call of climateQuery.useQuery.mock.calls) {
    const input = call[0] as { signal?: string; date?: string; renderForm?: string };
    if (input?.signal === signal) return input;
  }
  return undefined;
}

/** Whether the observer for one signal was enabled -- i.e. whether a request would go out. */
function enabledFor(signal: ClimateFieldSignalId): boolean | undefined {
  for (const call of climateQuery.useQuery.mock.calls) {
    const input = call[0] as { signal?: string };
    if (input?.signal === signal) return (call[1] as { enabled?: boolean })?.enabled;
  }
  return undefined;
}

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_LAYER_STATE = useLayerStore.getState();
const INITIAL_CLIMATE_STATE = useClimateStore.getState();

beforeEach(() => {
  climateQuery.useQuery.mockClear();
  drawnLayers.renders.length = 0;
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useLayerStore.setState({ ...INITIAL_LAYER_STATE, layerOpacity: {} }, true);
  useClimateStore.setState(INITIAL_CLIMATE_STATE, true);
  useTimeSliderStore.setState({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: capabilitiesWithDistinctDays(),
  });
});

afterEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useLayerStore.setState(INITIAL_LAYER_STATE, true);
  useClimateStore.setState(INITIAL_CLIMATE_STATE, true);
});

/** Switches the named signals on and renders the container against no map. */
function renderWithDrawn(signals: readonly ClimateFieldSignalId[]) {
  useMapStore.setState({
    activeLayers: signals.map((signal) => CLIMATE_FIELD_SIGNALS[signal].toggleId),
  });
  render(<ClimateFieldLayers map={null} bbox={BBOX} />);
}

describe("the nine climate rows read and draw independently", () => {
  it("reads every signal on its own row's newest day, never one day fanned across nine", () => {
    renderWithDrawn(CLIMATE_FIELD_SIGNAL_IDS);

    const days = CLIMATE_FIELD_SIGNAL_IDS.map((signal) => inputFor(signal)?.date);
    expect(days).toEqual(
      CLIMATE_FIELD_SIGNAL_IDS.map(
        (_signal, index) => `2026-07-${String(10 + index).padStart(2, "0")}`
      )
    );
    // And no two rows landed on the same day, which is the failure the union axis produced.
    expect(new Set(days).size).toBe(CLIMATE_FIELD_SIGNAL_IDS.length);
  });

  it("carries one row's scrub without moving any other row's day", () => {
    useTimeSliderStore.setState({
      layerDates: { [CLIMATE_FIELD_SIGNALS.precipitation.toggleId]: "2023-11-30" },
      forecastVariant: "monte_carlo",
      capabilities: capabilitiesWithDistinctDays(),
    });
    renderWithDrawn(CLIMATE_FIELD_SIGNAL_IDS);

    expect(inputFor("precipitation")?.date).toBe("2023-11-30");
    // Air temperature stays on its own stream's newest day. A shared climate day would have
    // dragged it back to 2023 with precipitation.
    expect(inputFor("air-temperature")?.date).toBe("2026-07-10");
  });

  it("enables the query only for the signals whose rows are switched on", () => {
    renderWithDrawn(["air-temperature", "wind-speed"]);

    expect(enabledFor("air-temperature")).toBe(true);
    expect(enabledFor("wind-speed")).toBe(true);
    // Registered but not fetched: the observer exists for every signal so the key is shared
    // with the dock's report, and `enabled` is what decides whether a request goes out.
    expect(enabledFor("precipitation")).toBe(false);
    expect(enabledFor("soil-wetness-profile")).toBe(false);
  });

  it("opens each signal on its own default form, so the drawn set composes", () => {
    renderWithDrawn(CLIMATE_FIELD_SIGNAL_IDS);

    // Exactly one signal defaults to a filled wash. Nine fills over the same 397 cells is one
    // visible field and eight buried under it, which is what the forms exist to prevent.
    const filled = CLIMATE_FIELD_SIGNAL_IDS.filter(
      (signal) => inputFor(signal)?.renderForm === "field"
    );
    expect(filled).toEqual([
      "air-temperature",
      "soil-wetness-surface",
      "soil-wetness-root-zone",
      "soil-wetness-profile",
    ]);
    expect(inputFor("dew-point")?.renderForm).toBe("isoline");
    expect(inputFor("precipitation")?.renderForm).toBe("symbol");
  });

  /**
   * The honesty guard, asserted at the client edge as well as the server's.
   *
   * A contour asserts the field varies smoothly between the samples it passes through. Daily
   * rainfall does not -- one 55 km square is wet and its neighbour dry -- and the soil-wetness
   * signals are measured on part of the lattice, so contouring them interpolates across ground
   * the lane never sampled. Both withhold `isoline`, and a store entry naming it anyway must
   * degrade to the signal's own default rather than reaching the map.
   */
  it("refuses a contour on the signals that withhold one, however the store was left", () => {
    useClimateStore.setState({
      renderForms: {
        precipitation: "isoline",
        "soil-wetness-profile": "isoline",
        "dew-point": "isoline",
      },
    });
    renderWithDrawn(["precipitation", "soil-wetness-profile", "dew-point"]);

    expect(inputFor("precipitation")?.renderForm).toBe("symbol");
    expect(inputFor("soil-wetness-profile")?.renderForm).toBe("field");
    // The signal that DOES offer contours still honours the same stored value.
    expect(inputFor("dew-point")?.renderForm).toBe("isoline");
  });

  it("gives every signal its own layer instance, keyed to its own signal and form", () => {
    renderWithDrawn(CLIMATE_FIELD_SIGNAL_IDS);

    const drawn = new Map(
      drawnLayers.renders.map((props) => [props.signal as string, props])
    );
    expect(new Set(drawn.keys())).toEqual(new Set(CLIMATE_FIELD_SIGNAL_IDS));
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      const props = drawn.get(signal);
      expect(props?.visible, signal).toBe(true);
      expect(props?.renderForm, signal).toBe(inputFor(signal)?.renderForm);
    }
  });
});
