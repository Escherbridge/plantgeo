import { beforeEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MapDateSummary } from "@/components/map/MapDateSummary";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { useDrawnLayerDayStore, type DrawnLayerDay } from "@/stores/useMetricAtDate";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import type { SliderCapabilities } from "@/types/time-slider";

/**
 * Deliberately nowhere near the machine's own date: a stray `new Date()` deciding "today"
 * flips every past/live/beyond-record assertion below.
 */
const SERVER_CURRENT_DATE = "2026-08-09";
/** Two days behind the server's today, so vegetation opens BEHIND the live edge on purpose. */
const VEGETATION_LATEST_DATE = "2026-08-07";
/** The scrubbed day the failing screenshot in the review was taken at. */
const VEGETATION_SCRUBBED_DAY = "2025-06-14";
/** Whole days from VEGETATION_SCRUBBED_DAY to SERVER_CURRENT_DATE, counted in UTC. */
const SCRUBBED_SPAN_DAYS = 421;

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 3,
  streamsUnavailable: false,
  layers: [
    {
      layerName: "fire-detections",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2023-01-01",
      latestObservedDate: SERVER_CURRENT_DATE,
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2024-01-01",
      latestObservedDate: SERVER_CURRENT_DATE,
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      layerName: "vegetation",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2022-08-01",
      latestObservedDate: VEGETATION_LATEST_DATE,
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
  ],
};

/**
 * Every store this surface reads, set BEFORE the render.
 *
 * `useViewedLayerDays` adopts the day key it finds on mount and only debounces LATER moves
 * through SCRUB_SETTLE_MS, so arranging the world first keeps every case below synchronous and
 * still exercises the real hook rather than a mock of it.
 */
function arrangeVisibleLayers(
  toggleIds: string[],
  options: { capabilities?: SliderCapabilities | null; layerDates?: Record<string, string> } = {}
) {
  const capabilities =
    options.capabilities === undefined ? CAPABILITIES : options.capabilities;
  useTimeSliderStore.setState({
    capabilities,
    layerDates: options.layerDates ?? {},
  });
  useMapStore.setState({ activeLayers: toggleIds });
}

/**
 * What the layers say they are PAINTING, as `LayerManager` publishes it.
 *
 * Written before the render for the same reason the stores above are: this surface reads it
 * through a plain selector, so arranging the world first keeps every case synchronous.
 */
function arrangeDrawnDays(drawnDays: Partial<Record<LayerToggleId, DrawnLayerDay>>) {
  useDrawnLayerDayStore.setState({ drawnDays });
}

function headlineText(): string {
  return screen.getByTestId("map-date-summary-headline").textContent ?? "";
}

function fullStatement(): string {
  return screen.getByTestId("map-date-summary-full").textContent ?? "";
}

beforeEach(() => {
  useTimeSliderStore.setState({ capabilities: null, layerDates: {} });
  useMapStore.setState({ activeLayers: [] });
  usePanelStore.setState({ layerPanelOpen: false });
  useDrawnLayerDayStore.setState({ drawnDays: {} });
});

describe("stating on the canvas what day the drawn layers are showing", () => {
  it("states the one day plainly when every visible layer shares it", () => {
    arrangeVisibleLayers(["fire", "water"]);

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(SERVER_CURRENT_DATE);
    // One day, on the live edge, nothing behind: one row of chrome and no second line.
    expect(screen.queryByTestId("map-date-summary-detail")).toBeNull();
    expect(fullStatement()).toContain(
      `All 2 visible layers are showing ${SERVER_CURRENT_DATE}`
    );
  });

  it("states a shared day once however many layers are drawing it", () => {
    arrangeVisibleLayers(["fire", "water", "weather"]);

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(SERVER_CURRENT_DATE);
    expect(fullStatement()).toContain("All 3 visible layers are showing");
  });

  it("states that the days differ, the span and the layer count when they do not match", () => {
    arrangeVisibleLayers(["fire", "vegetation"], {
      layerDates: { vegetation: VEGETATION_SCRUBBED_DAY },
    });

    renderWithProviders(<MapDateSummary />);

    // Never one date: naming any single day here is exactly the mislabelling a mixed-time
    // composite invites, and it is what the deleted pill would have done.
    expect(headlineText()).toBe("Mixed dates");
    const detail = screen.getByTestId("map-date-summary-detail").textContent ?? "";
    expect(detail).toContain(`${VEGETATION_SCRUBBED_DAY} – ${SERVER_CURRENT_DATE}`);
    expect(detail).toContain("2 layers");
    expect(detail).toContain(`${SCRUBBED_SPAN_DAYS} days apart`);
  });

  it("names every layer and its own day for a reader who cannot hover the span", () => {
    arrangeVisibleLayers(["fire", "vegetation"], {
      layerDates: { vegetation: VEGETATION_SCRUBBED_DAY },
    });

    renderWithProviders(<MapDateSummary />);

    const statement = fullStatement();
    expect(statement).toContain("mixed-time composite, not one moment");
    expect(statement).toContain(`Vegetation (NDVI): ${VEGETATION_SCRUBBED_DAY}`);
    expect(statement).toContain(`Fire Detections: ${SERVER_CURRENT_DATE}`);
  });

  it("states nothing at all when no layer is visible", () => {
    arrangeVisibleLayers([]);

    renderWithProviders(<MapDateSummary />);

    expect(screen.queryByTestId("map-date-summary")).toBeNull();
  });

  it("states nothing before capabilities arrive rather than guessing a day", () => {
    arrangeVisibleLayers(["fire", "vegetation"], { capabilities: null });

    renderWithProviders(<MapDateSummary />);

    expect(screen.queryByTestId("map-date-summary")).toBeNull();
  });

  it("leaves a layer that is switched off out of the span", () => {
    arrangeVisibleLayers(["fire", "water"], {
      layerDates: { vegetation: VEGETATION_SCRUBBED_DAY },
    });

    renderWithProviders(<MapDateSummary />);

    // Vegetation carries a scrubbed day but is not drawn, so it is not part of the composite.
    expect(headlineText()).toBe(SERVER_CURRENT_DATE);
    expect(fullStatement()).not.toContain(VEGETATION_SCRUBBED_DAY);
  });
});

describe("surviving the surfaces a reader can close", () => {
  it("states the same day whether the map manager is open or closed", () => {
    arrangeVisibleLayers(["vegetation"]);

    usePanelStore.setState({ layerPanelOpen: false });
    const closed = renderWithProviders(<MapDateSummary />);
    const dayWithDockClosed = headlineText();
    closed.unmount();

    usePanelStore.setState({ layerPanelOpen: true });
    renderWithProviders(<MapDateSummary />);

    // The failing case in the review is the closed dock: it is the only surface that carried a
    // date, so closing it left three different months looking like one moment.
    expect(dayWithDockClosed).toBe(VEGETATION_LATEST_DATE);
    expect(headlineText()).toBe(dayWithDockClosed);
  });
});

describe("naming where the drawn days stand against the server's today", () => {
  it("marks a shared day behind the server's today as a past day", () => {
    arrangeVisibleLayers(["vegetation"]);

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(VEGETATION_LATEST_DATE);
    expect(screen.queryByText("Past day")).not.toBeNull();
    expect(screen.queryByText("Beyond record")).toBeNull();
  });

  it("marks a day past the end of the record as beyond it", () => {
    arrangeVisibleLayers(["fire"], { layerDates: { fire: "2026-08-11" } });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe("2026-08-11");
    expect(screen.queryByText("Beyond record")).not.toBeNull();
  });

  it("marks nothing when every layer is on the live edge", () => {
    arrangeVisibleLayers(["fire", "water"]);

    renderWithProviders(<MapDateSummary />);

    expect(screen.queryByText("Past day")).toBeNull();
    expect(screen.queryByText("Beyond record")).toBeNull();
  });
});

/**
 * The surface states what is on the canvas, and the canvas lags the control.
 *
 * `LayerManager` retains the previous day's collection while the next one loads
 * (`keepPreviousData`), which is what stopped every scrub blanking the map -- and it means the
 * painted day and the slider's day differ for a whole warehouse round trip afterwards. This
 * surface read the slider until 2026-08-16, so it spent that round trip asserting a day the map
 * was not showing, and ordinary latency read as a data bug.
 */
describe("stating the day painted rather than the day requested", () => {
  it("names the day in hand while a newer one is still loading", () => {
    arrangeVisibleLayers(["vegetation"]);
    arrangeDrawnDays({
      vegetation: {
        drawnDate: "2026-08-05",
        requestedDate: VEGETATION_LATEST_DATE,
        isLoading: true,
      },
    });

    renderWithProviders(<MapDateSummary />);

    // The slider is on 2026-08-07 and the canvas is still showing 2026-08-05.
    expect(headlineText()).toBe("2026-08-05");
    expect(screen.queryByTestId("map-date-summary-loading")).not.toBeNull();
    expect(screen.getByTestId("map-date-summary-detail").textContent).toContain(
      "1 layer on an earlier day"
    );
    // And the day being waited on is named in full, so the lag is legible rather than merely
    // marked -- a reader who moved the thumb has to be able to tell a slow warehouse from a
    // wrong one.
    expect(fullStatement()).toContain(
      `Vegetation (NDVI): 2026-08-05 (loading ${VEGETATION_LATEST_DATE})`
    );
  });

  /**
   * A pan re-reads the SAME day over new ground. The map is loading and says so, but nothing
   * about the drawn day is in question, so the second line stays quiet: the two marks are
   * different claims and only one of them is about a date.
   */
  it("marks a pan as updating without claiming any layer is on an earlier day", () => {
    arrangeVisibleLayers(["vegetation"]);
    arrangeDrawnDays({
      vegetation: {
        drawnDate: VEGETATION_LATEST_DATE,
        requestedDate: VEGETATION_LATEST_DATE,
        isLoading: true,
      },
    });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(VEGETATION_LATEST_DATE);
    expect(screen.queryByTestId("map-date-summary-loading")).not.toBeNull();
    // No second line at all: one layer, on the day its row asks for, with nothing behind its
    // latest. The chip is the whole of what there is to say, which is what keeps the common
    // case one row of chrome.
    expect(screen.queryByTestId("map-date-summary-detail")).toBeNull();
    expect(fullStatement()).not.toContain("(loading");
  });

  /**
   * A feed whose read carries no day at all -- SSURGO is proxied per viewport -- publishes a
   * null drawn date and contributes only its loading state, so the row's own day still names it.
   */
  it("keeps the row's day for a feed that has no day of its own to draw", () => {
    arrangeVisibleLayers(["soil-survey"]);
    arrangeDrawnDays({
      "soil-survey": { drawnDate: null, requestedDate: null, isLoading: true },
    });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(SERVER_CURRENT_DATE);
    expect(screen.queryByTestId("map-date-summary-loading")).not.toBeNull();
  });

  /**
   * `LayerManager` publishes for the feeds it reads; `ClimateFieldLayers` owns nine more it does
   * not. An unreported layer keeps its row's day and claims nothing about loading, because
   * inventing a state for a layer nobody described is the same fabrication this surface exists
   * to prevent.
   */
  it("falls back to the row's day, and marks nothing, for a layer nothing has published", () => {
    arrangeVisibleLayers(["fire", "vegetation"]);

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe("Mixed dates");
    expect(screen.queryByTestId("map-date-summary-loading")).toBeNull();
    expect(fullStatement()).toContain(`Vegetation (NDVI): ${VEGETATION_LATEST_DATE}`);
    expect(fullStatement()).not.toContain("(loading");
  });

  it("marks nothing while every visible layer has settled on the day its row asks for", () => {
    arrangeVisibleLayers(["fire"]);
    arrangeDrawnDays({
      fire: {
        drawnDate: SERVER_CURRENT_DATE,
        requestedDate: SERVER_CURRENT_DATE,
        isLoading: false,
      },
    });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(SERVER_CURRENT_DATE);
    expect(screen.queryByTestId("map-date-summary-loading")).toBeNull();
    expect(screen.queryByTestId("map-date-summary-detail")).toBeNull();
  });

  /**
   * Offline, and the reason the second line is not worded "still loading".
   *
   * `fetchStatus: "paused"` leaves a retained frame standing with NOTHING in flight, so the drawn
   * day lags while `isLoading` is false -- and stays that way until connectivity returns, in an
   * app that ships an offline sync queue. Under the old wording the surface showed "1 layer still
   * loading" with no "Updating" chip beside it: two marks on one line disagreeing, the words
   * false, and no way for the reader to learn otherwise.
   */
  it("states an earlier day without claiming a fetch, when offline has paused one", () => {
    arrangeVisibleLayers(["vegetation"]);
    arrangeDrawnDays({
      vegetation: {
        drawnDate: "2026-08-05",
        requestedDate: VEGETATION_LATEST_DATE,
        isLoading: false,
      },
    });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe("2026-08-05");
    // Nothing is in flight, so nothing claims to be.
    expect(screen.queryByTestId("map-date-summary-loading")).toBeNull();
    // But the canvas is still behind its own control, and that is said plainly.
    const detail = screen.getByTestId("map-date-summary-detail").textContent ?? "";
    expect(detail).toContain("1 layer on an earlier day");
    expect(detail).not.toContain("loading");
    expect(fullStatement()).toContain(`(loading ${VEGETATION_LATEST_DATE})`);
  });
});

/**
 * One sentence names one day, qualifiers included.
 *
 * `useViewedLayerDays` answers "behind its latest" for the day each ROW is asking for, which is
 * right for the agent payload and wrong for a caption: carried over unchanged it put two
 * different days in one line. `resolveDrawnViewedDays` re-answers the same rule against the day
 * actually painted.
 */
describe("judging behind-its-latest on the day painted, not the day asked for", () => {
  it("still marks a retained frame as behind its latest after the row jumps to the newest day", () => {
    // The row is on vegetation's newest published day...
    arrangeVisibleLayers(["vegetation"]);
    // ...while the canvas is still painting a day two months back.
    arrangeDrawnDays({
      vegetation: {
        drawnDate: VEGETATION_SCRUBBED_DAY,
        requestedDate: VEGETATION_LATEST_DATE,
        isLoading: true,
      },
    });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(VEGETATION_SCRUBBED_DAY);
    // Carrying the row's answer over dropped this mark entirely: clicking "Latest" made the
    // "behind its latest" note disappear while the map went on drawing June 2025.
    expect(screen.getByTestId("map-date-summary-detail").textContent).toContain(
      "1 layer behind its latest"
    );
    expect(fullStatement()).toContain("(behind its latest)");
  });

  it("does not mark a day that IS the latest, however far back the row has been scrubbed", () => {
    arrangeVisibleLayers(["vegetation"], {
      layerDates: { vegetation: VEGETATION_SCRUBBED_DAY },
    });
    // The scrub has not landed yet, so what is painted is still the newest published day.
    arrangeDrawnDays({
      vegetation: {
        drawnDate: VEGETATION_LATEST_DATE,
        requestedDate: VEGETATION_SCRUBBED_DAY,
        isLoading: true,
      },
    });

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe(VEGETATION_LATEST_DATE);
    // The reverse failure: the row's answer said "behind its latest" about a day that IS the
    // latest, in the same sentence that named it.
    expect(fullStatement()).not.toContain("(behind its latest)");
    const detail = screen.getByTestId("map-date-summary-detail").textContent ?? "";
    expect(detail).not.toContain("behind its latest");
    expect(detail).toContain("1 layer on an earlier day");
  });
});

describe("counting the layers that are behind their own newest published day", () => {
  it("counts a layer scrubbed behind its latest", () => {
    arrangeVisibleLayers(["fire", "vegetation"], {
      layerDates: { vegetation: VEGETATION_SCRUBBED_DAY },
    });

    renderWithProviders(<MapDateSummary />);

    expect(screen.getByTestId("map-date-summary-detail").textContent).toContain(
      "1 layer behind its latest"
    );
    expect(fullStatement()).toContain(
      `Vegetation (NDVI): ${VEGETATION_SCRUBBED_DAY} (behind its latest)`
    );
  });

  it("counts no layer as behind when each is on its own newest day", () => {
    // Vegetation's newest day is two days behind today, so this is a mixed composite in which
    // nothing is stale -- the distinction the count exists to keep.
    arrangeVisibleLayers(["fire", "vegetation"]);

    renderWithProviders(<MapDateSummary />);

    expect(headlineText()).toBe("Mixed dates");
    const detail = screen.getByTestId("map-date-summary-detail").textContent ?? "";
    expect(detail).toContain("2 days apart");
    expect(detail).not.toContain("behind");
    expect(fullStatement()).not.toContain("behind its latest");
  });

  it("does not count a layer whose newest published day the server never named", () => {
    // `soil-survey` names no warehouse stream (it is proxied per viewport from USDA), so
    // nothing measures its latest; it falls back to the server's today and must not be
    // reported as stale on a claim nobody made. This stood on `soil` until that toggle was
    // given a permanentlyUnavailableReason -- a withheld layer is not on screen at all, so it
    // is correctly absent from a summary of what IS being shown.
    arrangeVisibleLayers(["soil-survey", "vegetation"]);

    renderWithProviders(<MapDateSummary />);

    expect(fullStatement()).not.toContain(
      "Soil Survey (SSURGO): 2026-08-09 (behind its latest)"
    );
    expect(fullStatement()).toContain("Soil Survey (SSURGO): 2026-08-09");
  });
});
