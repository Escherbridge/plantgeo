import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import {
  SCRUB_SETTLE_MS,
  useMetricAtDate,
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
        useMetricAtDate({ layerId: "water", metric: "streamflow", fetchMetricAtDate, enabled }),
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
