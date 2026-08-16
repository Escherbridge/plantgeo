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
import {
  resolveLayerDate,
  UNINITIALIZED_DATE,
  useTimeSliderStore,
} from "@/stores/time-slider-store";

const SERVER_CURRENT_DATE = "2026-08-04";

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2026-08-02",
      latestObservedDate: "2026-08-03",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      layerName: "fire-perimeters",
      temporalKind: "event",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2026-06-17",
      latestObservedDate: "2026-07-21",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
  ],
};

beforeEach(() => {
  useTimeSliderStore.setState({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,
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
    // a second surface competing with the sliders on the layer rows.
    expect(container.innerHTML).toBe("");
    expect(screen.queryByTestId("time-slider")).toBeNull();
  });

  /**
   * Rewritten from "leaves the day uninitialized until the server payload lands", which read
   * `state.selectedDate` -- one map-wide day, replaced on 2026-08-09 by a day per layer. The
   * claim is unchanged and is still the important one: without a payload there is no honest
   * "today", and a browser-clock fallback would put it on the wrong day outside UTC. It is now
   * asserted through `resolveLayerDate`, which is where a layer's day actually comes from.
   */
  it("leaves every layer's day uninitialized until the server payload lands", () => {
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    const state = useTimeSliderStore.getState();
    expect(state.capabilities).toBeNull();
    // Sparse and empty: nothing is seeded, so no layer holds an override nobody set.
    expect(state.layerDates).toEqual({});
    expect(resolveLayerDate(state.layerDates, state.capabilities, "water")).toBe(
      UNINITIALIZED_DATE
    );
    // In flight is not the same as failed, and only the latter may interrupt the sliders.
    expect(state.capabilitiesUnavailable).toBe(false);
  });

  /**
   * Rewritten from "writes the payload into the store and takes the day from it". The payload
   * write is unchanged; what the day is taken from is not. A layer with a capability now opens
   * on its OWN `latestObservedDate` rather than on the server's today -- that is the whole point
   * of per-layer dates -- and only a layer with no capability row falls back to the server's
   * today, which is the part of the old assertion still worth making.
   */
  it("writes the payload into the store, from which each layer takes its own day", () => {
    capabilitiesQuery.mockReturnValue({ data: CAPABILITIES, isError: false });
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    const state = useTimeSliderStore.getState();
    expect(state.capabilities).toEqual(CAPABILITIES);
    // `water` -> "water-gauges", which published up to 2026-08-03.
    expect(resolveLayerDate(state.layerDates, state.capabilities, "water")).toBe("2026-08-03");
    // `fire-perimeters` published up to 2026-07-21 -- a different day, from the same payload.
    expect(resolveLayerDate(state.layerDates, state.capabilities, "fire-perimeters")).toBe(
      "2026-07-21"
    );
    // `drought` has no geo.layers row, so it falls back to the server's today -- never
    // Date.now(), which is the guarantee this component exists to hold.
    expect(resolveLayerDate(state.layerDates, state.capabilities, "drought")).toBe(
      SERVER_CURRENT_DATE
    );
    // Nothing was seeded into `layerDates` by the payload landing: an eager copy would become a
    // silent override the moment the next payload moved a layer's newest day.
    expect(state.layerDates).toEqual({});
  });

  it("polls on the same interval the read-model memoizes for", () => {
    renderWithProviders(<TimeSliderCapabilitiesLoader />);

    // `serverCurrentDate` rides in this payload and rolls over at UTC midnight, an instant no
    // user action coincides with; without the interval a tab open across it keeps reporting
    // yesterday as today. The refetch is a server-side cache hit, so it costs nothing upstream.
    const [, options] = capabilitiesQuery.mock.calls[0] as [unknown, Record<string, unknown>];
    expect(options.staleTime).toBe(5 * 60_000);

    // Two clocks, chosen per payload. A COMPLETE payload polls on the read-model's own memo
    // interval, so the poll is a server-side cache hit that only re-stamps the date.
    const refetchInterval = options.refetchInterval as (query: {
      state: { data: { streamsUnavailable: boolean } | undefined };
    }) => number;
    expect(refetchInterval({ state: { data: { streamsUnavailable: false } } })).toBe(5 * 60_000);
    expect(refetchInterval({ state: { data: undefined } })).toBe(5 * 60_000);

    // A SHORT payload is known-incomplete rather than merely old -- the server answered without
    // its stream scan, so thirteen layers are missing an axis they really have. That scan lands
    // in the server cache seconds later, so waiting the full five minutes to collect it would
    // hold every stream-backed slider in the outage state long after the outage ended.
    expect(refetchInterval({ state: { data: { streamsUnavailable: true } } })).toBe(30_000);
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
