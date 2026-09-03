import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useMapStore } from "@/stores/map-store";
import { useLayerStore } from "@/stores/layer-store";
import { useClimateStore } from "@/stores/climate-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { SCRUB_SETTLE_MS, useDrawnLayerDayStore } from "@/stores/useMetricAtDate";
import type { Map as MapLibreMap } from "maplibre-gl";
import {
  climateFieldStreamName,
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
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
//
// Answers PER SIGNAL rather than one result for all nine. Nine rows retaining nine independent
// days is the whole point of the split, so a stub that could only answer once for all of them
// could not tell "each row named its own drawn day" from "one day fanned across nine".
const climateQuery = vi.hoisted(() => {
  const resultBySignal = new Map<string, Record<string, unknown>>();
  return {
    resultBySignal,
    useQuery: vi.fn((_input: unknown, _options: unknown) => {
      const signal = (_input as { signal?: string } | undefined)?.signal ?? "";
      return resultBySignal.get(signal) ?? { data: undefined };
    }),
  };
});

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
    streamsUnavailable: false,
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
  climateQuery.resultBySignal.clear();
  useDrawnLayerDayStore.setState({ drawnDays: {}, publications: {} });
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

/**
 * Switches the named signals on and renders the container against no map.
 *
 * `zoom` is a parameter and not the fixed 9 it used to be: zoom selects the physical rung that
 * answers, and only the detail rung may serve the form that was asked for -- so a case about
 * the served form has to be able to stand on either side of that line.
 */
function renderWithDrawn(signals: readonly ClimateFieldSignalId[], zoom = 9) {
  useMapStore.setState({
    activeLayers: signals.map((signal) => CLIMATE_FIELD_SIGNALS[signal].toggleId),
  });
  return render(<ClimateFieldLayers map={null} bbox={BBOX} zoom={zoom} />);
}

/** A response that answered for the key it was asked with. */
function landed(): Record<string, unknown> {
  return {
    data: { type: "FeatureCollection", features: [] },
    isSuccess: true,
    isFetching: false,
    isPlaceholderData: false,
  };
}

/** A response still standing in from the PREVIOUS key while the current one loads. */
function retaining(): Record<string, unknown> {
  return {
    data: { type: "FeatureCollection", features: [] },
    isSuccess: true,
    isFetching: true,
    isPlaceholderData: true,
  };
}

/** Waits out one row's scrub settle window, which is what turns a new day into a request. */
async function settleScrub(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, SCRUB_SETTLE_MS + 20));
  });
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

    // Six of the nine default to a filled wash and three to contours, since `symbol` was
    // withdrawn from every signal on 2026-09-02 -- the frozen render contract permits no point
    // form for a continuous field, and precipitation's old `symbol` default went with it. The
    // composite is thinner than it was; what it is not is dishonest about the ground each mark
    // covers.
    const filled = CLIMATE_FIELD_SIGNAL_IDS.filter(
      (signal) => inputFor(signal)?.renderForm === "field"
    );
    expect(filled).toEqual([
      "air-temperature",
      "precipitation",
      "soil-wetness-surface",
      "soil-wetness-root-zone",
      "soil-wetness-profile",
    ]);
    expect(inputFor("dew-point")?.renderForm).toBe("isoline");
  });

  it("offers no signal the point form the render contract forbids for a continuous field", () => {
    // Asserted over the whole table rather than on the one signal that used to default to it: a
    // dot's radius says nothing about the ground a value describes, which is exactly the
    // fictitious footprint `LAYER_RENDER_CONTRACT` withholds every point form for here.
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      expect(CLIMATE_FIELD_SIGNALS[signal].renderForms, signal).not.toContain("symbol");
    }
  });

  it("draws a stored points preference as the filled field rather than as another signal's default", () => {
    // The store outlives the vocabulary: every persisted precipitation row written before
    // 2026-09-02 names `symbol`. Falling through to each signal's own head would hand a reader
    // who explicitly chose points the CONTOURS that `dew-point` opens on, so `symbol` maps onto
    // `field` by name -- the honest successor, one mark per measured cell either way.
    useClimateStore.setState({
      renderForms: { precipitation: "symbol", "dew-point": "symbol" },
    });
    renderWithDrawn(["precipitation", "dew-point"]);

    expect(inputFor("precipitation")?.renderForm).toBe("field");
    expect(inputFor("dew-point")?.renderForm).toBe("field");
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

    expect(inputFor("precipitation")?.renderForm).toBe("field");
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

/**
 * Each row draws the form that ARRIVED, never the form it asked for.
 *
 * Only the z13 rung may answer in the requested form: a coarse rung has neither the lattice
 * pitch a square needs nor the regular lattice a contour needs, so `tierRenderForm`
 * (parquet-climate-field.ts) served `symbol` -- Point geometry -- for every form below it. A row
 * that kept painting the REQUESTED form then built `fill`/`line` layers over Points, which
 * MapLibre draws as nothing at all while `ClimateDetails` went on reporting aggregated cells:
 * an empty canvas that reads as missing coverage rather than as a zoom-out.
 *
 * The coarse rungs are tessellated fills now (the pitch comes from the shared tier table), so the
 * degrade the cases below exercise is a legacy or replayed answer rather than the live one -- and
 * the rule they pin is unchanged and still load-bearing: paint what ARRIVED, never what was asked
 * for. The `zoomTier` the answer declares travels the same way, for the same reason.
 */
describe("each climate row draws the form the server actually served", () => {
  /** A landed collection that declares which signal it answered for and in which form. */
  function servedAs(
    signal: ClimateFieldSignalId,
    renderForm: ClimateRenderForm,
    zoomTier = 13
  ) {
    return {
      ...landed(),
      data: { type: "FeatureCollection", features: [], signal, renderForm, zoomTier },
    };
  }

  /** The newest props one signal's layer instance was rendered with. */
  function lastDrawnFor(signal: ClimateFieldSignalId): Record<string, unknown> | undefined {
    for (let index = drawnLayers.renders.length - 1; index >= 0; index--) {
      const props = drawnLayers.renders[index];
      if ((props.signal as string) === signal) return props;
    }
    return undefined;
  }

  it("paints the degraded points form when a coarse rung answers with Points", () => {
    climateQuery.resultBySignal.set(
      "air-temperature",
      servedAs("air-temperature", "symbol")
    );
    renderWithDrawn(["air-temperature"], 9);

    // The request still asks for the reader's chosen form; the SERVED form is what is painted.
    expect(inputFor("air-temperature")?.renderForm).toBe("field");
    expect(lastDrawnFor("air-temperature")?.renderForm).toBe("symbol");
  });

  it("paints the requested form once the detail rung answers in it", () => {
    climateQuery.resultBySignal.set("air-temperature", servedAs("air-temperature", "field"));
    renderWithDrawn(["air-temperature"], 13);

    expect(inputFor("air-temperature")?.renderForm).toBe("field");
    expect(lastDrawnFor("air-temperature")?.renderForm).toBe("field");
  });

  /**
   * The rung that ANSWERED, never the rung the row asked for. The layer sizes its outline off
   * this, and the two differ for a frame after every zoom -- adopting the requested one would
   * stroke a coarse tessellation as though it were the detail lattice.
   */
  it("passes the rung the collection declares, not the one this row asked for", () => {
    climateQuery.resultBySignal.set("air-temperature", servedAs("air-temperature", "field", 9));
    renderWithDrawn(["air-temperature"], 9);

    expect(lastDrawnFor("air-temperature")?.zoomTier).toBe(9);
  });

  /**
   * No rung at all until one has answered. `BASE_ZOOM_TIER` is the single value that turns the
   * per-cell outline on (`ClimateFieldLayer`, "DETAIL RUNG ONLY"), so standing in with it stroked
   * every seam of whatever collection arrived first -- a five-degree z0 tessellation included.
   */
  it("declares no rung until an answer has arrived, rather than standing in with the detail one", () => {
    renderWithDrawn(["air-temperature"], 9);

    expect(lastDrawnFor("air-temperature")?.zoomTier).toBeNull();
    expect(lastDrawnFor("air-temperature")?.zoomTier).not.toBe(13);
  });

  /**
   * The read-back guard, which is the same one `ClimateDetails` applies: react-query serves the
   * previous key's data for a frame after a form change, and that answer describes a different
   * request. Adopting its form would repaint this row from another signal's response.
   */
  it("ignores a collection that answered for a different signal", () => {
    climateQuery.resultBySignal.set("air-temperature", servedAs("dew-point", "symbol", 0));
    renderWithDrawn(["air-temperature"], 9);

    expect(lastDrawnFor("air-temperature")?.renderForm).toBe("field");
    // ...and takes no rung from it either: z0 would have been another signal's answer deciding
    // whether THIS row strokes its cells.
    expect(lastDrawnFor("air-temperature")?.zoomTier).toBeNull();
  });

  /**
   * The half that makes the three cases above matter: what `renderForm` actually BUILDS.
   *
   * The real `ClimateFieldLayer` is imported past this file's stub, because the defect was not
   * "the wrong prop was passed" -- it was that a `fill` and a `line` over Point features draw
   * nothing, silently, with no MapLibre error to find.
   */
  describe("the form decides the MapLibre layer built over the served geometry", () => {
    /** Records what a real `ClimateFieldLayer` asks MapLibre to build. */
    function createRecordingMap() {
      const layers = new Map<string, { id: string; type: string }>();
      const sources = new Map<string, { setData: () => void }>();
      const added: { id: string; type: string }[] = [];
      const recorder = {
        isStyleLoaded: () => true,
        getStyle: () => ({ layers: [] }),
        on: () => {},
        off: () => {},
        getSource: (id: string) => sources.get(id),
        addSource: (id: string) => {
          sources.set(id, { setData: () => {} });
        },
        getLayer: (id: string) => layers.get(id),
        addLayer: (layer: { id: string; type: string }) => {
          layers.set(layer.id, layer);
          added.push(layer);
        },
        removeLayer: (id: string) => {
          layers.delete(id);
        },
        removeSource: (id: string) => {
          sources.delete(id);
        },
        setPaintProperty: () => {},
      };
      // The narrow stand-in cast every fake-map case in this suite makes: these are the only
      // members the layer's effects touch, and widening the fake to the full Map is noise.
      return { map: recorder as unknown as MapLibreMap, added };
    }

    async function renderRealLayer(
      renderForm: ClimateRenderForm,
      zoomTier: ZoomTier | null = 13
    ) {
      const { ClimateFieldLayer } = await vi.importActual<
        typeof import("@/components/map/layers/ClimateFieldLayer")
      >("@/components/map/layers/ClimateFieldLayer");
      const { map, added } = createRecordingMap();
      render(
        <ClimateFieldLayer
          map={map}
          signal="air-temperature"
          renderForm={renderForm}
          zoomTier={zoomTier}
        />
      );
      return added.map((layer) => layer.type);
    }

    it("builds a circle layer for the served points form, never a fill or a line", async () => {
      expect(await renderRealLayer("symbol")).toEqual(["circle"]);
    });

    it("builds the fill and its outline only when the served form is the filled one", async () => {
      expect(await renderRealLayer("field")).toEqual(["fill", "line"]);
    });

    /**
     * The coarse rungs are FILLED now, where wave 1 drew nothing at all below z13 unless the
     * server had degraded them to points. The fill is what closes the acceptance gate
     * "continuous fields fill polygons rather than drawing contour strokes only".
     */
    it("builds the fill for the served form at a coarse rung", async () => {
      expect(await renderRealLayer("field", 9)).toEqual(["fill"]);
    });

    /**
     * And no per-cell outline there. Every rung tessellates the whole viewport now, so a stroke on
     * every cell draws a mesh of block seams across it -- the defect this track exists to remove.
     * At the detail rung the same stroke still says something true, which the case above pins.
     */
    it("draws no per-cell outline at a coarse rung, where it would read as block seams", async () => {
      expect(await renderRealLayer("field", 5)).not.toContain("line");
      expect(await renderRealLayer("field", 0)).not.toContain("line");
    });

    /**
     * And none before any rung has answered. The outline is a claim about the detail lattice, and
     * a null rung is the caller saying it does not yet know which lattice arrived -- which is not
     * the same as knowing it is the detail one.
     */
    it("draws no per-cell outline while the rung is still unknown", async () => {
      expect(await renderRealLayer("field", null)).toEqual(["fill"]);
    });

    /**
     * `isoband` on the render contract's vocabulary: a dissolved BAND is a closed area, and the
     * spec's gate is that a continuous field fills it. Wave 1 stroked the boundary and filled
     * nothing, which drew a contour map where a filled field was owed.
     */
    it("fills a dissolved band and draws its boundary over the fill", async () => {
      expect(await renderRealLayer("isoline", 9)).toEqual(["fill", "line"]);
    });
  });
});

/**
 * Each row says which day it is PAINTING, not which day it is asking for.
 *
 * `useClimateFieldQuery` holds the previous answer while the next loads (`keepPreviousData`), so
 * these nine layers retain the previous day's isobands through every scrub. Retaining without
 * reporting is strictly worse than neither: the canvas caption (`MapDateSummary`) and the row's
 * own pending indicator (`DockSections`, which reads `drawnDays[layerId]?.isLoading`) both go
 * silently wrong -- one states the day the slider asks for over the day before it, the other
 * never lights at all. `LayerManager` reads none of these nine, so each row publishes its own.
 */
describe("each climate row labels the day it is actually painting", () => {
  function publishedFor(signal: ClimateFieldSignalId) {
    return useDrawnLayerDayStore.getState().drawnDays[CLIMATE_FIELD_SIGNALS[signal].toggleId];
  }

  /**
   * The coverage assertion, walking the signal list rather than a list spelled here: a tenth
   * signal added without a publication would fail this case instead of shipping a dead row
   * indicator and a caption that names the wrong day.
   */
  it("publishes a drawn day for every signal that is switched on", () => {
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      climateQuery.resultBySignal.set(signal, landed());
    }
    renderWithDrawn(CLIMATE_FIELD_SIGNAL_IDS);

    for (const [index, signal] of CLIMATE_FIELD_SIGNAL_IDS.entries()) {
      const day = `2026-07-${String(10 + index).padStart(2, "0")}`;
      expect(publishedFor(signal), signal).toEqual({
        drawnDate: day,
        requestedDate: day,
        isLoading: false,
      });
    }
  });

  it("publishes nothing for a signal whose row is switched off", () => {
    // The disabled-query shape: TanStack keeps `isPlaceholderData` true off `keepPreviousData`
    // even once a query stops running, so an off row would otherwise report itself mid-load for
    // good and leave its indicator lit with nothing able to clear it.
    climateQuery.resultBySignal.set("precipitation", { ...retaining(), isFetching: false });
    renderWithDrawn(["air-temperature"]);

    expect(publishedFor("precipitation")).toBeUndefined();
    expect(publishedFor("air-temperature")).toBeDefined();
  });

  /**
   * H2 itself: the row has moved and the isobands on screen are still the previous day's.
   * Scrubbing precipitation from its own newest day back to 2023 must leave the publication
   * naming the day in hand, and must not disturb any other row's.
   */
  it("keeps naming the day in hand while a retained frame is on screen", async () => {
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      climateQuery.resultBySignal.set(signal, landed());
    }
    const rendered = renderWithDrawn(CLIMATE_FIELD_SIGNAL_IDS);
    const precipitationIndex = CLIMATE_FIELD_SIGNAL_IDS.indexOf("precipitation");
    const paintedDay = `2026-07-${String(10 + precipitationIndex).padStart(2, "0")}`;
    expect(publishedFor("precipitation")?.drawnDate).toBe(paintedDay);

    climateQuery.resultBySignal.set("precipitation", retaining());
    act(() => {
      useTimeSliderStore
        .getState()
        .setLayerDate(CLIMATE_FIELD_SIGNALS.precipitation.toggleId, "2023-11-30");
    });
    await settleScrub();
    rendered.rerender(<ClimateFieldLayers map={null} bbox={BBOX} zoom={9} />);

    expect(publishedFor("precipitation")).toEqual({
      drawnDate: paintedDay,
      requestedDate: "2023-11-30",
      isLoading: true,
    });
    // Nine publishers, nine disjoint sets: one row's scrub must not touch another's entry.
    expect(publishedFor("air-temperature")).toEqual({
      drawnDate: "2026-07-10",
      requestedDate: "2026-07-10",
      isLoading: false,
    });
  });

  /**
   * Offline. `fetchStatus: "paused"` leaves the retained isobands standing with nothing in
   * flight, so the drawn day still lags while nothing is loading -- and it stays that way until
   * connectivity returns, in an app that ships an offline sync queue. A publication reporting
   * this as loading would light every affected row's indicator indefinitely.
   */
  it("reports a paused request as an earlier day painted, not as loading", async () => {
    climateQuery.resultBySignal.set("precipitation", landed());
    const rendered = renderWithDrawn(["precipitation"]);
    const precipitationIndex = CLIMATE_FIELD_SIGNAL_IDS.indexOf("precipitation");
    const paintedDay = `2026-07-${String(10 + precipitationIndex).padStart(2, "0")}`;

    climateQuery.resultBySignal.set("precipitation", { ...retaining(), isFetching: false });
    act(() => {
      useTimeSliderStore
        .getState()
        .setLayerDate(CLIMATE_FIELD_SIGNALS.precipitation.toggleId, "2023-11-30");
    });
    await settleScrub();
    rendered.rerender(<ClimateFieldLayers map={null} bbox={BBOX} zoom={9} />);

    expect(publishedFor("precipitation")).toEqual({
      drawnDate: paintedDay,
      requestedDate: "2023-11-30",
      isLoading: false,
    });
  });
});
