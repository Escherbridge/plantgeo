import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
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
  });
  capabilitiesQuery.mockReturnValue({ data: undefined });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TimeSliderPanel", () => {
  it("renders nothing and leaves the day uninitialized until the server payload lands", () => {
    renderWithProviders(<TimeSliderPanel />);

    expect(screen.queryByTestId("time-slider")).toBeNull();
    expect(useTimeSliderStore.getState().capabilities).toBeNull();
    // A browser-clock fallback would put "today" on the wrong day outside UTC.
    expect(useTimeSliderStore.getState().selectedDate).toBe(UNINITIALIZED_DATE);
  });

  it("feeds the capability payload into the store, which is what makes the slider reachable", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    renderWithProviders(<TimeSliderPanel />);

    expect(useTimeSliderStore.getState().capabilities).toEqual(CAPABILITIES);
    // The server's today, never Date.now().
    expect(useTimeSliderStore.getState().selectedDate).toBe(SERVER_CURRENT_DATE);
    expect(screen.getByTestId("time-slider")).toBeTruthy();
    expect(screen.getByTestId("time-slider-selected-date").textContent).toBe(SERVER_CURRENT_DATE);
  });

  it("mounts the right-hand panel region and its time section with no panel open at all", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    // Nothing here opens a panel: the region is rendered unconditionally, because the selected
    // day applies to every layer and must not be something a user opens a panel to reach.
    renderWithProviders(<TimeSliderPanel />);

    const region = screen.getByTestId("map-panel-region");
    expect(region.getAttribute("aria-label")).toBe("Map time and panel region");
    // Pinned at the top of the region, and the only section there when no body is docked.
    expect(screen.getByTestId("map-panel-region-time").contains(screen.getByTestId("time-slider")))
      .toBe(true);
    expect(screen.queryByTestId("map-panel-region-body")).toBeNull();
    // One scroller, on the region -- a scroller nested inside a scroller is how the slider
    // would become unreachable on a phone.
    expect(region.className).toContain("overflow-y-auto");
  });

  it("keeps the region mounted while the payload is still in flight", () => {
    renderWithProviders(<TimeSliderPanel />);

    // The slider itself renders nothing without capabilities; the region still exists, so the
    // marker appears in place rather than shifting the layout when the payload lands.
    expect(screen.getByTestId("map-panel-region")).toBeTruthy();
    expect(screen.queryByTestId("time-slider")).toBeNull();
  });

  it("starts the axis at the earliest date the server published, not at a client guess", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES });
    renderWithProviders(<TimeSliderPanel />);

    const [firstDayLabel] = screen.getAllByText("2026-06-17");
    expect(firstDayLabel).toBeTruthy();
    expect(screen.getByTestId("time-slider-axis-rule").textContent).toContain(
      "as the data publisher dated it"
    );
  });
});
