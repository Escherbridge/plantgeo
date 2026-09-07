/**
 * The control surface a UI calls, exercised through a REAL QueryClient with the real persister
 * installed and a real (fake-backed) IndexedDB behind it: a manual layer serves its second read
 * from disk with no network, and `refetchNow` is what puts it back on the network.
 */
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useLayerCacheControls } from "@/hooks/useLayerCacheControls";
import {
  resetLayerCachePolicyForTests,
  useLayerCachePolicyStore,
} from "@/lib/cache/layer-cache-policy-store";
import {
  createIndexedDbLayerQueryPersister,
  resetCacheAccounting,
} from "@/lib/cache/query-persister";
import { resetSyncIndexForTests } from "@/stores/sync-index-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "../lib/cache/fake-indexeddb";

/** The key `@trpc/react-query` builds for `environmental.getDroughtClassification.useQuery`. */
const DROUGHT_KEY = [
  ["environmental", "getDroughtClassification"],
  { input: { bbox: "0,0,1,1", date: "2026-08-01" }, type: "query" },
] as const;

const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();

beforeEach(() => {
  globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
  resetCacheAccounting();
  resetLayerCachePolicyForTests();
  resetSyncIndexForTests();
  useTimeSliderStore.setState({
    capabilities: {
      serverCurrentDate: "2026-08-14",
      futureAxisDays: 0,
      layers: [],
      streamsUnavailable: false,
    },
  });
});

afterEach(() => {
  globalThis.indexedDB = originalIndexedDb;
  resetLayerCachePolicyForTests();
  resetSyncIndexForTests();
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

/**
 * Mounts the layer's query and its cache control together, on one client whose persister is the
 * production one. `staleTime: 0` so react-query itself never masks a refetch we asked for.
 */
async function renderLayerWithControls(queryFn: () => Promise<unknown>) {
  let client: QueryClient | null = null;
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        persister: createIndexedDbLayerQueryPersister(() => client),
      },
    },
  });
  client = queryClient;

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  let hook!: ReturnType<typeof renderHook<ReturnType<typeof useLayerCacheControls>, unknown>>;
  await act(async () => {
    hook = renderHook(
      () => {
        useQuery({ queryKey: DROUGHT_KEY, queryFn });
        return useLayerCacheControls("drought");
      },
      { wrapper: Wrapper }
    );
  });
  return { hook, queryClient };
}

describe("useLayerCacheControls", () => {
  it("reports the nature-derived default without the user setting anything", async () => {
    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    const { hook } = await renderLayerWithControls(queryFn);

    expect(hook.result.current.nature).toBe("release_series");
    expect(hook.result.current.refreshMode).toBe("manual");
    expect(hook.result.current.retainedDayLimit).toBe(180);
    expect(hook.result.current.isOverridden).toBe(false);
    hook.unmount();
  });

  it("records when the layer last actually reached the network", async () => {
    const before = Date.now();
    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    const { hook } = await renderLayerWithControls(queryFn);

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(queryFn).toHaveBeenCalledTimes(1);
    const lastFetchedAt = useLayerCachePolicyStore.getState().lastFetchedAt.drought;
    expect(lastFetchedAt).toBeDefined();
    expect(lastFetchedAt as number).toBeGreaterThanOrEqual(before);
    hook.unmount();
  });

  it("refetchNow puts a manual layer back on the network; nothing else does", async () => {
    const queryFn = vi.fn().mockResolvedValue({
      type: "FeatureCollection",
      features: [],
      availability: "published",
      reason: null,
    });
    const { hook, queryClient } = await renderLayerWithControls(queryFn);

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(queryFn).toHaveBeenCalledTimes(1);

    // An invalidation on its own -- the ordinary react-query path a remount or a window focus
    // would take -- is answered from disk, because the layer is manual.
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: DROUGHT_KEY });
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(queryFn, "a manual layer refetched without being asked").toHaveBeenCalledTimes(1);

    // The user asking is what breaks through.
    await act(async () => {
      await hook.result.current.refetchNow();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(queryFn).toHaveBeenCalledTimes(2);
    hook.unmount();
  });

  it("persists a mode change the reload test in layer-cache-policy asserts survives", async () => {
    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    const { hook } = await renderLayerWithControls(queryFn);

    act(() => {
      hook.result.current.setRefreshMode("automatic");
    });

    expect(hook.result.current.refreshMode).toBe("automatic");
    expect(hook.result.current.isOverridden).toBe(true);
    expect(useLayerCachePolicyStore.getState().overrides.drought).toEqual({
      refreshMode: "automatic",
    });
    hook.unmount();
  });

  it("applies a retention limit through the control and reports it back", async () => {
    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    const { hook } = await renderLayerWithControls(queryFn);

    await act(async () => {
      await hook.result.current.setRetainedDayLimit(3);
    });

    expect(hook.result.current.retainedDayLimit).toBe(3);
    expect(hook.result.current.isOverridden).toBe(true);
    hook.unmount();
  });
});
