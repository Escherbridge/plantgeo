import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import type { SliderCapabilities } from "@/types/time-slider";

/**
 * The tRPC hook is stubbed rather than driven over a link: this asserts the WIRING -- that the
 * loader reads environmental.getSliderCapabilities and feeds the store -- and a unit test must
 * never open a socket to do it. Same shape the deleted TimeSliderPanel.test.tsx used, since it
 * is the same fetch; only the component that owns it changed (2026-08-08).
 */
const capabilitiesQuery = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => ({
    data: undefined as SliderCapabilities | undefined,
    isError: false,
  }))
);

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: { getSliderCapabilities: { useQuery: capabilitiesQuery } },
  },
}));

import TimeSliderCapabilitiesLoader from "@/components/map/TimeSliderCapabilitiesLoader";
import { UNINITIALIZED_DATE, useTimeSliderStore } from "@/stores/time-slider-store";

const SERVER_CURRENT_DATE = "2026-08-04";

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
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
    focusedLayerName: null,
  });
  capabilitiesQuery.mockReturnValue({ data: undefined, isError: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TimeSliderCapabilitiesLoader", () => {
  it("renders nothing at all", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES, isError: false });
    const { container } = renderWithProviders(<TimeSliderCapabilitiesLoader />);

    // Headless by design: it exists to own a fetch, not to draw. Anything it rendered would be
    // a second surface competing with the pill and the dock's Time section.
    expect(container.innerHTML).toBe("");
    expect(screen.queryByTestId("time-slider")).toBeNull();
  });

  it("leaves the day uninitialized until the server payload lands", () => {
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    expect(useTimeSliderStore.getState().capabilities).toBeNull();
    // A browser-clock fallback would put "today" on the wrong day outside UTC.
    expect(useTimeSliderStore.getState().selectedDate).toBe(UNINITIALIZED_DATE);
    // In flight is not the same as failed, and only the latter may interrupt the slider.
    expect(useTimeSliderStore.getState().capabilitiesUnavailable).toBe(false);
  });

  it("writes the payload into the store and takes the day from it", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES, isError: false });
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    expect(useTimeSliderStore.getState().capabilities).toEqual(CAPABILITIES);
    // The server's today, never Date.now().
    expect(useTimeSliderStore.getState().selectedDate).toBe(SERVER_CURRENT_DATE);
  });

  it("polls on the same interval the read-model memoizes for", () => {
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    // `serverCurrentDate` rides in this payload and rolls over at UTC midnight, an instant no
    // user action coincides with; without the interval a tab open across it keeps reporting
    // yesterday as today. The refetch is a server-side cache hit, so it costs nothing upstream.
    const [, options] = capabilitiesQuery.mock.calls[0] as [unknown, Record<string, unknown>];
    expect(options.staleTime).toBe(5 * 60_000);
    expect(options.refetchInterval).toBe(5 * 60_000);
    // The layer list moves only when an ingest run lands a new day, so a focus refetch would
    // spend the whole-warehouse scan for an answer that has not changed.
    expect(options.refetchOnWindowFocus).toBe(false);
  });

  it("flags unavailable only when the fetch has never once succeeded", () => {
    capabilitiesQuery.mockReturnValue({ data: undefined, isError: true });
    const { unmount } = renderWithProviders(<TimeSliderCapabilitiesLoader />);

    expect(useTimeSliderStore.getState().capabilitiesUnavailable).toBe(true);
    unmount();

    // react-query keeps `data` across a background refetch failure, so a transient poll error
    // once a payload exists must leave the slider drawing from the stale-but-real capabilities
    // rather than flipping a working control into an error state.
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES, isError: true });
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    expect(useTimeSliderStore.getState().capabilitiesUnavailable).toBe(false);
  });

  it("clears the failure flag as soon as any payload lands", () => {
    useTimeSliderStore.setState({ capabilitiesUnavailable: true });
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES, isError: false });
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    // A payload landed, however late: whatever the fetch history was, it is no longer true
    // that the slider has nothing to show.
    expect(useTimeSliderStore.getState().capabilitiesUnavailable).toBe(false);
    expect(useTimeSliderStore.getState().capabilities).toEqual(CAPABILITIES);
  });
});
