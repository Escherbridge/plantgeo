import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import type { SliderCapabilities } from "@/types/time-slider";

/**
 * The tRPC hook is stubbed rather than driven over a link: this asserts the WIRING -- that
 * the panel reads environmental.getSliderCapabilities and feeds the store -- and a unit test
 * must never open a socket to do it.
 */
const capabilitiesQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => ({ data: undefined as SliderCapabilities | undefined }))
);

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: { getSliderCapabilities: { useQuery: capabilitiesQuery } },
  },
}));

import TimeSliderPanel from "@/components/map/TimeSliderPanel";
import { UNINITIALIZED_DATE, useTimeSliderStore } from "@/stores/time-slider-store";

const SERVER_CURRENT_DATE = "2026-08-04";

const CAPABILITIES: SliderCapabilities = {
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
      earliestObservedDate: "2026-08-02",
    },
    {
      layerName: "fire-perimeters",
      temporalKind: "event",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2026-06-17",
    },
  ],
};

beforeEach(() => {
  useTimeSliderStore.setState({
    selectedDate: UNINITIALIZED_DATE,
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,
  });
  capabilitiesQuery.mockReturnValue({ data: undefined });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TimeSliderPanel", () => {
  it("renders no time bar and leaves the day uninitialized until the server payload lands", () => {
    renderWithProviders(<TimeSliderPanel />);

    expect(screen.queryByTestId("time-slider")).toBeNull();
    expect(screen.queryByTestId("time-slider-toggle")).toBeNull();
    expect(useTimeSliderStore.getState().capabilities).toBeNull();
    // A browser-clock fallback would put "today" on the wrong day outside UTC.
    expect(useTimeSliderStore.getState().selectedDate).toBe(UNINITIALIZED_DATE);
  });

  it("feeds the capability payload into the store and marks the day in the collapsed bar", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    renderWithProviders(<TimeSliderPanel />);

    expect(useTimeSliderStore.getState().capabilities).toEqual(CAPABILITIES);
    // The server's today, never Date.now().
    expect(useTimeSliderStore.getState().selectedDate).toBe(SERVER_CURRENT_DATE);
    // Collapsed by default: the pill is the always-visible marker, the card is opened detail.
    expect(screen.getByTestId("time-slider-toggle-date").textContent).toBe(SERVER_CURRENT_DATE);
    expect(screen.getByTestId("time-slider-toggle").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("map-panel-region-time-controls").className).toContain("hidden");
  });

  it("expands to the full scrubber on demand, and collapses back", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    renderWithProviders(<TimeSliderPanel />);

    fireEvent.click(screen.getByTestId("time-slider-toggle"));
    expect(screen.getByTestId("time-slider-toggle").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("map-panel-region-time-controls").className).not.toContain(
      "hidden"
    );
    // The day lives in the date field now that it is typable, not in a read-only span.
    expect((screen.getByTestId("time-slider-date-input") as HTMLInputElement).value).toBe(
      SERVER_CURRENT_DATE
    );

    fireEvent.click(screen.getByTestId("time-slider-toggle"));
    expect(screen.getByTestId("time-slider-toggle").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("map-panel-region-time-controls").className).toContain("hidden");
  });

  it("mounts the right-hand panel region and its time bar with no panel open at all", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    // Nothing here opens a panel: the region is rendered unconditionally, because the selected
    // day applies to every layer and must not be something a user opens a panel to reach.
    renderWithProviders(<TimeSliderPanel />);

    const region = screen.getByTestId("map-panel-region");
    expect(region.getAttribute("aria-label")).toBe("Map time and panel region");
    // Pinned at the top of the region, and the only section there when no body is docked.
    expect(
      screen.getByTestId("map-panel-region-time").contains(screen.getByTestId("time-slider-toggle"))
    ).toBe(true);
    expect(screen.queryByTestId("map-panel-region-body")).toBeNull();
    // One scroller, on the region -- a scroller nested inside a scroller is how the slider
    // would become unreachable on a phone.
    expect(region.className).toContain("overflow-y-auto");
    // Collapsed, the region is a mostly-empty column floating over the canvas; without the
    // pass-through it would swallow every map drag that starts under it.
    expect(region.className).toContain("pointer-events-none");
  });

  it("keeps the region mounted while the payload is still in flight", () => {
    renderWithProviders(<TimeSliderPanel />);

    // The bar renders nothing without capabilities; the region still exists, so the marker
    // appears in place rather than shifting the layout when the payload lands.
    expect(screen.getByTestId("map-panel-region")).toBeTruthy();
    expect(screen.queryByTestId("time-slider-toggle")).toBeNull();
  });

  it("starts the axis at the earliest date the server published, not at a client guess", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    renderWithProviders(<TimeSliderPanel />);
    fireEvent.click(screen.getByTestId("time-slider-toggle"));

    const [firstDayLabel] = screen.getAllByText("2026-06-17");
    expect(firstDayLabel).toBeTruthy();
    expect(screen.getByTestId("time-slider-axis-rule").textContent).toContain(
      "as the data publisher dated it"
    );
  });

  it("flags an off-today day in the collapsed bar and offers a one-tap way back", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    renderWithProviders(<TimeSliderPanel />);
    // A past observed day: the pill must say the map is NOT drawing the live day, because an
    // off-today date silently filters every layer.
    useTimeSliderStore.getState().setSelectedDate("2026-08-02");

    expect(screen.getByText("Past day")).toBeTruthy();
    const barToday = screen.getByTestId("time-slider-bar-today");
    fireEvent.click(barToday);
    expect(useTimeSliderStore.getState().selectedDate).toBe(SERVER_CURRENT_DATE);
    // Back at the live day the bar goes quiet again: no chip, no Today button.
    expect(screen.queryByTestId("time-slider-bar-today")).toBeNull();
    expect(screen.queryByText("Past day")).toBeNull();
  });

  it("names an outright capabilities failure in the bar instead of rendering nothing", () => {
    renderWithProviders(<TimeSliderPanel />);
    // The store flag is what the panel's own error effect sets; driving it directly keeps
    // this a render-state test rather than a react-query lifecycle test.
    useTimeSliderStore.getState().setCapabilitiesUnavailable(true);

    expect(screen.getByText("Time range unavailable")).toBeTruthy();
    // Expanding shows TimeSlider's full notice, which names it a loading failure.
    fireEvent.click(screen.getByTestId("time-slider-toggle"));
    expect(screen.getByTestId("time-slider-unavailable")).toBeTruthy();
  });
});
