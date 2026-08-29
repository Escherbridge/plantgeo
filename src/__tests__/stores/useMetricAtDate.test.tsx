import {
  keepPreviousData,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import {
  drawnDayFlagsFromQuery,
  SCRUB_SETTLE_MS,
  useDrawnLayerDayStore,
  useMetricAtDate,
  usePublishedDrawnLayerDays,
  type LiveLayerDayReport,
  type MetricAtDateFetcher,
} from "@/stores/useMetricAtDate";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import type { MetricAtDateCollection, SliderCapabilities } from "@/types/time-slider";

/**
 * `water`'s capability -- `warehouseLayerNameFor` maps the `water` toggle to `water-gauges`,
 * see `src/lib/map/layer-registry.ts`. Both scrubbed-to days below stay inside this axis, so
 * the store's own clamp (`clampDateToDomain`) never rewrites what this test asks for.
 */
const WATER_CAPABILITY: SliderCapabilities["layers"][number] = {
  layerName: "water-gauges",
  temporalKind: "daily_series",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2026-08-01",
  latestObservedDate: "2026-08-08",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: "2026-08-08",
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [WATER_CAPABILITY],
};

function publishedCollection(date: string): MetricAtDateCollection {
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [0, 0] },
        properties: { date },
      },
    ],
    availability: "published",
    reason: null,
  };
}

/**
 * Mounts inside an async `act`, not just `renderHook`'s own synchronous one: the first query
 * here can resolve on the very next microtask (a `Promise.resolve()`-backed fetcher), which
 * lands in the gap between `renderHook` returning and this test's first explicit `act` call
 * otherwise -- an update React logs as unwrapped even though nothing here is unobserved.
 */
async function renderWithClient(fetchMetricAtDate: MetricAtDateFetcher) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }
  let hook!: ReturnType<
    typeof renderHook<ReturnType<typeof useMetricAtDate>, { enabled: boolean }>
  >;
  await act(async () => {
    hook = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useMetricAtDate({
          layerId: "water",
          metric: "perimeter-acres",
          fetchMetricAtDate,
          enabled,
        }),
      { wrapper: Wrapper, initialProps: { enabled: true as boolean } }
    );
    await Promise.resolve();
  });
  return hook;
}

beforeEach(() => {
  vi.useFakeTimers();
  useTimeSliderStore.setState({
    layerDates: { water: "2026-08-05" },
    forecastVariant: "monte_carlo",
    capabilities: CAPABILITIES,
    capabilitiesUnavailable: false,
  });
});

afterEach(() => {
  useTimeSliderStore.setState({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,
  });
  vi.useRealTimers();
});

describe("useMetricAtDate names the day the collection in hand actually describes", () => {
  it("reports the day just scrubbed to, not the previous settled day, once the query is disabled", async () => {
    const fetchMetricAtDate = vi.fn(async (input) => publishedCollection(input.date));
    const { result, rerender } = await renderWithClient(fetchMetricAtDate);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCRUB_SETTLE_MS);
    });
    expect(result.current.collection.features).toHaveLength(1);
    expect(result.current.resolvedDate).toBe("2026-08-05");
    expect(result.current.isShowingPreviousDay).toBe(false);

    // Scrub to a THIRD day -- outside `PREFETCH_RADIUS_DAYS` of "2026-08-05", so the earlier
    // settle's own neighbour-prefetch (±1 day) never warmed this key -- and disable the query in
    // the same beat, e.g. the row's eye going off mid-scrub. `shouldQuery` flips false on
    // `enabled`, not on availability, so `availabilityBeforeQuery` for the new day is still
    // "published"; the only thing that must change is which day the (now-empty) collection is
    // labelled with.
    act(() => {
      useTimeSliderStore.getState().setLayerDate("water", "2026-08-08");
    });
    rerender({ enabled: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCRUB_SETTLE_MS);
    });

    // Not a retained frame: `shouldQuery` is false, so the `!shouldQuery` branch runs and hands
    // back a fresh (empty) collection rather than the placeholder TanStack is still holding.
    expect(result.current.isShowingPreviousDay).toBe(false);
    expect(result.current.collection.features).toHaveLength(0);
    // The bug: `describedDate` used to key off `query.isPlaceholderData` alone, which
    // `keepPreviousData` leaves `true` even once the query is DISABLED for a key that was never
    // fetched, so this reported "2026-08-05" -- the day the last successful fetch described --
    // underneath a collection that actually describes "2026-08-08".
    expect(result.current.resolvedDate).toBe("2026-08-08");
  });

  it("still names the previous settled day while a real load is in flight for the new one", async () => {
    // Keyed by date, not a single captured resolver: `useMetricAtDate` also prefetches the
    // neighbouring day on every settle (`PREFETCH_RADIUS_DAYS`), so a single shared variable
    // would get silently overwritten by that prefetch's resolver before this test ever reads it.
    const pendingResolvers = new Map<string, (collection: MetricAtDateCollection) => void>();
    const fetchMetricAtDate = vi.fn((input) => {
      if (input.date === "2026-08-05") return Promise.resolve(publishedCollection(input.date));
      return new Promise<MetricAtDateCollection>((resolve) => {
        pendingResolvers.set(input.date, resolve);
      });
    });
    const { result } = await renderWithClient(fetchMetricAtDate);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCRUB_SETTLE_MS);
    });
    expect(result.current.collection.features).toHaveLength(1);

    // Scrub to a second PUBLISHED day with the query left enabled throughout: this is the
    // genuine retained-frame case `isShowingPreviousDay` exists for, contrasted with the
    // disabled-query case above.
    act(() => {
      useTimeSliderStore.getState().setLayerDate("water", "2026-08-06");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCRUB_SETTLE_MS);
    });

    expect(result.current.isShowingPreviousDay).toBe(true);
    expect(result.current.resolvedDate).toBe("2026-08-05");

    await act(async () => {
      pendingResolvers.get("2026-08-06")?.(publishedCollection("2026-08-06"));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.resolvedDate).toBe("2026-08-06");
    expect(result.current.isShowingPreviousDay).toBe(false);
  });
});

/**
 * The same rule, for the layers that do not go through the hook above.
 *
 * Every live layer on the map is read by `LayerManager` through a tRPC hook per feed, so no one
 * place owns both a request and the collection it produced -- and `MapDateSummary`, which states
 * the map's day on canvas, sits in a different subtree entirely. This registry is how the day in
 * hand reaches it: the readers publish, the caption reads, and nothing has to infer a drawn day
 * from a control position.
 */
describe("publishing what each layer is actually drawing", () => {
  function renderReports(reports: LiveLayerDayReport[], publisher: "layer-manager" = "layer-manager") {
    return renderHook(
      ({ current }: { current: LiveLayerDayReport[] }) =>
        usePublishedDrawnLayerDays(publisher, current),
      { initialProps: { current: reports } }
    );
  }

  /** A layer whose collection has landed for the day its row asks for. */
  function settled(layerId: "water" | "drought", date: string): LiveLayerDayReport {
    return {
      layerId,
      isDrawn: true,
      requestedDate: date,
      isFetching: false,
      hasLandedForRequestedDate: true,
      isShowingPreviousDay: false,
    };
  }

  /** A layer whose next day is loading, with the previous day's features still painted. */
  function retaining(layerId: "water" | "drought", date: string): LiveLayerDayReport {
    return {
      layerId,
      isDrawn: true,
      requestedDate: date,
      isFetching: true,
      hasLandedForRequestedDate: false,
      isShowingPreviousDay: true,
    };
  }

  beforeEach(() => {
    useDrawnLayerDayStore.setState({ drawnDays: {}, publications: {} });
  });

  it("names the requested day once that day's collection is the one in hand", () => {
    renderReports([settled("water", "2026-08-05")]);

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-05",
      isLoading: false,
    });
  });

  it("keeps naming the previous day while a retained frame stands in, then adopts the new one", () => {
    const { rerender } = renderReports([settled("water", "2026-08-05")]);

    rerender({ current: [retaining("water", "2026-08-06")] });

    // The row has moved; the features on screen have not. Naming the row's day here is the one
    // misstatement this registry exists to prevent.
    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-06",
      isLoading: true,
    });

    rerender({ current: [settled("water", "2026-08-06")] });

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-06",
      requestedDate: "2026-08-06",
      isLoading: false,
    });
  });

  /**
   * TanStack keeps `isPlaceholderData` true off `keepPreviousData` even once a query is
   * DISABLED -- the trap `resolvedDate` documents above -- so a layer the reader switched off
   * would report itself permanently mid-load and leave a mark on the canvas nothing could clear.
   */
  it("publishes nothing for a layer that is not drawn, however its query still reads", () => {
    renderReports([
      {
        layerId: "water",
        isDrawn: false,
        requestedDate: "2026-08-05",
        isFetching: false,
        hasLandedForRequestedDate: false,
        isShowingPreviousDay: true,
      },
    ]);

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toBeUndefined();
  });

  it("publishes a feed with no day of its own as a loading state alone", () => {
    renderReports([
      {
        layerId: "water",
        isDrawn: true,
        requestedDate: null,
        isFetching: true,
        hasLandedForRequestedDate: false,
        isShowingPreviousDay: false,
      },
    ]);

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: null,
      requestedDate: null,
      isLoading: true,
    });
  });

  /**
   * The failure case, which `!isPlaceholderData` cannot see. A request that ERRORS has
   * `isPlaceholderData: false` exactly as a successful one does, so testing for the absence of a
   * placeholder records a day that never arrived -- and then names it over the NEXT request's
   * retained frame, which paints the day before the failure. That is the surface stating a day
   * the canvas is not showing: precisely what this registry exists to prevent.
   */
  it("does not record a day whose request failed, and does not name it over the next retained frame", () => {
    const { rerender } = renderReports([settled("water", "2026-08-05")]);

    // 2026-08-06 errors: nothing is painted, and no placeholder stands in.
    rerender({
      current: [
        {
          layerId: "water",
          isDrawn: true,
          requestedDate: "2026-08-06",
          isFetching: false,
          hasLandedForRequestedDate: false,
          isShowingPreviousDay: false,
        },
      ],
    });
    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-06",
      requestedDate: "2026-08-06",
      // Nothing is in flight, so nothing is loading -- the layer is simply empty.
      isLoading: false,
    });

    // The reader moves on to a third day. Its placeholder repaints 2026-08-05's features, and
    // 2026-08-05 is what must be named -- never the day that failed in between.
    rerender({ current: [retaining("water", "2026-08-07")] });

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-07",
      isLoading: true,
    });
  });

  /**
   * Offline. `fetchStatus: "paused"` leaves a retained frame standing with nothing in flight, so
   * the drawn day still lags while `isLoading` is false -- and stays that way until connectivity
   * returns. A publication that reported loading here would put an "Updating" mark on the canvas
   * that nothing could clear.
   */
  it("reports a paused request as an earlier day being painted, not as loading", () => {
    const { rerender } = renderReports([settled("water", "2026-08-05")]);

    rerender({
      current: [
        {
          layerId: "water",
          isDrawn: true,
          requestedDate: "2026-08-06",
          isFetching: false,
          hasLandedForRequestedDate: false,
          isShowingPreviousDay: true,
        },
      ],
    });

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-06",
      isLoading: false,
    });
  });

  /**
   * A background refresh of the day already painted -- `staleTime` expiring, or `useFireData`'s
   * two-minute poll -- is a fetch nobody is waiting on. Reporting it blinked an "Updating" mark
   * over an idle map every two minutes.
   */
  it("does not report a refresh of the day already painted as loading", () => {
    renderReports([{ ...settled("water", "2026-08-05"), isFetching: true }]);

    expect(useDrawnLayerDayStore.getState().drawnDays.water).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-05",
      isLoading: false,
    });
  });

  /**
   * Two readers, disjoint sets. `LayerManager` owns nine feeds and each `ClimateSignalLayer`
   * owns its own, so a store that replaced the whole record on every publication would have the
   * two erase each other on alternate renders.
   */
  it("keeps one publisher's entries when another publishes", () => {
    renderReports([settled("water", "2026-08-05")]);
    renderHook(() =>
      usePublishedDrawnLayerDays("climate-precipitation", [
        {
          layerId: "climate-precipitation",
          isDrawn: true,
          requestedDate: "2026-08-04",
          isFetching: false,
          hasLandedForRequestedDate: true,
          isShowingPreviousDay: false,
        },
      ])
    );

    const published = useDrawnLayerDayStore.getState().drawnDays;
    expect(published.water?.drawnDate).toBe("2026-08-05");
    expect(published["climate-precipitation"]?.drawnDate).toBe("2026-08-04");
  });

  /**
   * The publication is rebuilt on every render of a component that re-renders on every viewport
   * tick, so an unchanged one must not wake the caption. The equality check lives in the store
   * rather than in a dependency array over ~20 query flags.
   */
  it("hands back the same record when nothing has moved", () => {
    const { rerender } = renderReports([settled("water", "2026-08-05")]);
    const published = useDrawnLayerDayStore.getState().drawnDays;

    rerender({ current: [settled("water", "2026-08-05")] });

    expect(useDrawnLayerDayStore.getState().drawnDays).toBe(published);
  });

  it("clears the publication once the reader unmounts", () => {
    const { unmount } = renderReports([settled("water", "2026-08-05")]);
    expect(useDrawnLayerDayStore.getState().drawnDays.water).toBeDefined();

    unmount();

    expect(useDrawnLayerDayStore.getState().drawnDays).toEqual({});
  });
});

/**
 * The same ledger, driven by a REAL react-query observer rather than by hand-built reports.
 *
 * The cases above state what the hook does with a given set of flags; this one proves the flags
 * themselves. The whole H1 defect lived in a premise about TanStack that reads as obviously true
 * and is false -- `isPlaceholderData` is not "a placeholder is showing", it is "the data in hand
 * did not come from this key", and an ERRORED query satisfies neither. No amount of hand-written
 * report fixtures can catch that, because they encode the author's belief about the flag.
 */
describe("the drawn-day ledger against a real query observer", () => {
  /** What the transport does for a given day. Absent means resolve. */
  type Outcome = "resolve" | "reject" | "hang";

  /**
   * A `useQuery` wired to the registry exactly as every live layer is: real observer, real
   * `keepPreviousData`, flags derived by `drawnDayFlagsFromQuery` rather than spelled out.
   */
  function renderPublishingQuery(outcomes: Map<string, Outcome>) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // A hung request's own resolver, so a case can land the day it was waiting on. Mutating
    // `outcomes` cannot do it: the promise for that key is already in flight, and react-query
    // will not call `queryFn` again for a query it is still fetching.
    const resolvers = new Map<string, (value: { date: string }) => void>();
    function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
    }
    const hook = renderHook(
      ({ date }: { date: string }) => {
        const query = useQuery({
          queryKey: ["drawn-day-probe", date],
          queryFn: () => {
            const outcome = outcomes.get(date) ?? "resolve";
            if (outcome === "reject") return Promise.reject(new Error(`nothing for ${date}`));
            if (outcome === "hang") {
              return new Promise<{ date: string }>((resolve) => resolvers.set(date, resolve));
            }
            return Promise.resolve({ date });
          },
          placeholderData: keepPreviousData,
        });
        usePublishedDrawnLayerDays("layer-manager", [
          {
            layerId: "water",
            isDrawn: true,
            requestedDate: date,
            ...drawnDayFlagsFromQuery(query),
          },
        ]);
        return query;
      },
      { wrapper: Wrapper, initialProps: { date: "2026-08-05" } }
    );
    return Object.assign(hook, { resolvers });
  }

  /** Lets the transport's microtasks and react-query's own scheduling run. */
  async function flush(): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  function publishedWater() {
    return useDrawnLayerDayStore.getState().drawnDays.water;
  }

  beforeEach(() => {
    useDrawnLayerDayStore.setState({ drawnDays: {}, publications: {} });
  });

  it("does not record a failed day, and paints the last day that landed once a placeholder returns", async () => {
    const outcomes = new Map<string, Outcome>([
      ["2026-08-06", "reject"],
      ["2026-08-07", "hang"],
    ]);
    const { result, rerender } = renderPublishingQuery(outcomes);
    await flush();

    expect(publishedWater()).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-05",
      isLoading: false,
    });

    // The premise, pinned against the installed TanStack rather than assumed: an ERRORED query
    // reports `isPlaceholderData: false` exactly as a successful one does. Testing for the
    // absence of a placeholder therefore reads a failure as a landing.
    rerender({ date: "2026-08-06" });
    await flush();
    expect(result.current.isError).toBe(true);
    expect(result.current.isPlaceholderData).toBe(false);
    expect(result.current.data).toBeUndefined();
    // Nothing is painted and nothing is in flight. The day asked for is named because that is
    // what a blank layer is showing -- indistinguishable, correctly, from a day the warehouse
    // holds nothing for. What must NOT happen is this day entering the ledger.
    expect(publishedWater()).toEqual({
      drawnDate: "2026-08-06",
      requestedDate: "2026-08-06",
      isLoading: false,
    });

    // The reader moves on. TanStack's placeholder is the last query with defined data, so the
    // canvas repaints 2026-08-05's features -- and 2026-08-05 is what must be named. Before the
    // fix the ledger held 2026-08-06, so the surface stated the failed day over the day before
    // it: the exact misstatement this registry exists to prevent.
    rerender({ date: "2026-08-07" });
    await flush();
    expect(result.current.isPlaceholderData).toBe(true);
    expect(publishedWater()).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-07",
      isLoading: true,
    });
  });

  it("names the previous day while a real placeholder stands in, then adopts the day that lands", async () => {
    const outcomes = new Map<string, Outcome>([["2026-08-06", "hang"]]);
    const { result, rerender, resolvers } = renderPublishingQuery(outcomes);
    await flush();

    rerender({ date: "2026-08-06" });
    await flush();
    expect(result.current.isPlaceholderData).toBe(true);
    expect(publishedWater()).toEqual({
      drawnDate: "2026-08-05",
      requestedDate: "2026-08-06",
      isLoading: true,
    });

    await act(async () => {
      resolvers.get("2026-08-06")?.({ date: "2026-08-06" });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(publishedWater()).toEqual({
      drawnDate: "2026-08-06",
      requestedDate: "2026-08-06",
      isLoading: false,
    });
  });
});
