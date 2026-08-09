import { beforeEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MapDateSummary } from "@/components/map/MapDateSummary";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
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
    // `soil` names no warehouse stream, so nothing measures its latest; it falls back to the
    // server's today and must not be reported as stale on a claim nobody made.
    arrangeVisibleLayers(["soil", "vegetation"]);

    renderWithProviders(<MapDateSummary />);

    expect(fullStatement()).not.toContain("Soil Properties: 2026-08-09 (behind its latest)");
    expect(fullStatement()).toContain("Soil Properties: 2026-08-09");
  });
});
