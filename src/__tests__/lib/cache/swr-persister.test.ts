import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, type QueryFunctionContext } from "@tanstack/react-query";
import { setEntry, getEntry } from "@/lib/cache/indexeddb-store";
import {
  CACHE_SCHEMA_VERSION,
  REVALIDATION_MIN_INTERVAL_MS,
  STORE_CONFIG,
  createIndexedDbLayerQueryPersister,
  getActiveRevalidationsCount,
  resetCacheAccounting,
  revalidateAgainstDW,
  type StoredLayerQueryEntry,
} from "@/lib/cache/query-persister";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "./fake-indexeddb";

function trpcQueryKey(path: string[], input?: Record<string, unknown>): readonly unknown[] {
  return input === undefined ? [path] : [path, { input, type: "query" }];
}

function makeQuery(queryKey: readonly unknown[], queryHash: string) {
  return { queryKey, queryHash } as unknown as Parameters<
    ReturnType<typeof createIndexedDbLayerQueryPersister>
  >[2];
}

/** Polls until `predicate` holds; background revalidation settles over several event-loop turns. */
async function waitUntil(predicate: () => boolean, attempts = 100): Promise<void> {
  for (let index = 0; index < attempts; index += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  expect(predicate()).toBe(true);
}

const FAKE_CONTEXT = {} as QueryFunctionContext;
const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();
const SERVER_TODAY = "2026-08-14";

describe("SWR IndexedDB DW Reconciliation & Throttling", () => {
  beforeEach(() => {
    globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
    resetCacheAccounting();
    useTimeSliderStore.setState({
      capabilities: {
        serverCurrentDate: SERVER_TODAY,
        futureAxisDays: 0,
        layers: [],
        streamsUnavailable: false,
      },
    });
  });

  afterEach(() => {
    globalThis.indexedDB = originalIndexedDb;
    useTimeSliderStore.setState(initialTimeSliderState, true);
  });

  it("serves the stored entry instantly and publishes the revalidated value into react-query", async () => {
    // Defect C2: the revalidated payload used to reach IndexedDB and stop there, so the layer
    // rendered one fetch behind until the query key was mounted again.
    const key = trpcQueryKey(["environmental", "getClimateField"], {
      bbox: "0,0,1,1",
      date: SERVER_TODAY,
    });
    const query = makeQuery(key, "swr-instant-hit");
    const initialPayload = { type: "FeatureCollection", features: [{ id: 1 }] };

    const createdAt = Date.now() - 2 * REVALIDATION_MIN_INTERVAL_MS;
    const storedEntry: StoredLayerQueryEntry = {
      key: "swr-instant-hit",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt,
      expiresAt: Date.now() + 60_000,
      lastRevalidatedAt: createdAt,
      approxByteSize: 100,
      value: initialPayload,
    };
    await setEntry(STORE_CONFIG, "swr-instant-hit", storedEntry);

    const revalidatedPayload = { type: "FeatureCollection", features: [{ id: 1 }, { id: 2 }] };
    const queryFn = vi.fn().mockResolvedValue(revalidatedPayload);

    const queryClient = new QueryClient();
    queryClient.setQueryData(key, initialPayload);
    const persister = createIndexedDbLayerQueryPersister(() => queryClient);

    const result = await persister(queryFn, FAKE_CONTEXT, query);
    expect(result).toEqual(initialPayload);

    // react-query applies structural sharing on the way in, so compare by value, not identity.
    await waitUntil(
      () => (queryClient.getQueryData(key) as { features?: unknown[] } | undefined)?.features?.length === 2
    );
    expect(queryClient.getQueryData(key)).toEqual(revalidatedPayload);

    const updatedStored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "swr-instant-hit");
    expect(updatedStored?.value).toEqual(revalidatedPayload);
  });

  it("revalidates a HISTORICAL day, because this warehouse rewrites past days", async () => {
    // The correction path. A gate that skipped revalidation for historical days shipped for one
    // afternoon and meant a reopened/republished day could not reach the reader for 30 days --
    // while the sync track drew "saved on this device" over the stale answer.
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-05",
    });
    const query = makeQuery(key, "swr-historical");
    const createdAt = Date.now() - 10 * REVALIDATION_MIN_INTERVAL_MS;
    await setEntry(STORE_CONFIG, "swr-historical", {
      key: "swr-historical",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt,
      expiresAt: Date.now() + 60_000,
      lastRevalidatedAt: createdAt,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [] },
    } satisfies StoredLayerQueryEntry);

    const corrected = { type: "FeatureCollection", features: [{ id: "reopened" }] };
    const queryFn = vi.fn().mockResolvedValue(corrected);
    const persister = createIndexedDbLayerQueryPersister(() => null);
    const served = await persister(queryFn, FAKE_CONTEXT, query);

    // The hit is still instant; the correction follows in the background.
    expect(served).toEqual({ type: "FeatureCollection", features: [] });
    await waitUntil(() => queryFn.mock.calls.length === 1);
  });

  it("writes the corrected historical payload back to disk", async () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,2,2",
      date: "2026-08-05",
    });
    const createdAt = Date.now() - 10 * REVALIDATION_MIN_INTERVAL_MS;
    const stored: StoredLayerQueryEntry = {
      key: "swr-historical-write",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt,
      expiresAt: Date.now() + 60_000,
      lastRevalidatedAt: createdAt,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [] },
    };
    await setEntry(STORE_CONFIG, "swr-historical-write", stored);

    const corrected = { type: "FeatureCollection", features: [{ id: "reopened" }] };
    const result = await revalidateAgainstDW(
      vi.fn().mockResolvedValue(corrected),
      FAKE_CONTEXT,
      key,
      "swr-historical-write",
      stored
    );

    expect(result).toEqual(corrected);
    const updated = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "swr-historical-write");
    expect(updated?.value).toEqual(corrected);
  });

  it("publishes nothing into a query react-query no longer holds", async () => {
    const key = trpcQueryKey(["environmental", "getClimateField"], {
      bbox: "0,0,2,2",
      date: SERVER_TODAY,
    });
    const stored: StoredLayerQueryEntry = {
      key: "swr-unheld",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now(),
      expiresAt: Date.now() + 60_000,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [] },
    };
    await setEntry(STORE_CONFIG, "swr-unheld", stored);
    const queryClient = new QueryClient();

    await revalidateAgainstDW(
      vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [{ id: 7 }] }),
      FAKE_CONTEXT,
      key,
      "swr-unheld",
      stored,
      () => queryClient
    );

    // Materialising a cache entry for a query nothing observes would only give the collector work.
    expect(queryClient.getQueryData(key)).toBeUndefined();
  });

  it("abandons a queued revalidation whose entry was replaced while it waited", async () => {
    // A revalidation can wait behind the concurrency throttle for longer than LIVE_TTL_MS; by
    // then the entry may have expired, missed, and been replaced by a NEWER fetch. Writing the
    // older result would revert a layer the reader just watched update.
    const key = trpcQueryKey(["environmental", "getStreamflow"], {
      bbox: "0,0,1,1",
      date: SERVER_TODAY,
    });
    const staleSnapshot: StoredLayerQueryEntry = {
      key: "swr-generation",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now() - 600_000,
      expiresAt: Date.now() + 60_000,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [{ id: "old" }] },
    };
    const newerOnDisk: StoredLayerQueryEntry = {
      ...staleSnapshot,
      // A cold write after the expiry+miss resets `createdAt`; that is the generation marker.
      createdAt: Date.now(),
      value: { type: "FeatureCollection", features: [{ id: "newer" }] },
    };
    await setEntry(STORE_CONFIG, "swr-generation", newerOnDisk);

    const queryClient = new QueryClient();
    queryClient.setQueryData(key, newerOnDisk.value);

    const result = await revalidateAgainstDW(
      vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [{ id: "overtaken" }] }),
      FAKE_CONTEXT,
      key,
      "swr-generation",
      staleSnapshot,
      () => queryClient
    );

    expect(result).toEqual(staleSnapshot.value);
    expect(queryClient.getQueryData(key)).toEqual(newerOnDisk.value);
    const onDisk = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "swr-generation");
    expect(onDisk?.value).toEqual(newerOnDisk.value);
  });

  it("does not revalidate the same entry twice inside the minimum interval", async () => {
    const key = trpcQueryKey(["environmental", "getStreamflow"], {
      bbox: "0,0,1,1",
      date: SERVER_TODAY,
    });
    const query = makeQuery(key, "swr-recent");
    await setEntry(STORE_CONFIG, "swr-recent", {
      key: "swr-recent",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now() - 10 * REVALIDATION_MIN_INTERVAL_MS,
      expiresAt: Date.now() + 60_000,
      lastRevalidatedAt: Date.now() - 1_000,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [] },
    } satisfies StoredLayerQueryEntry);

    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    const persister = createIndexedDbLayerQueryPersister(() => null);
    await persister(queryFn, FAKE_CONTEXT, query);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(queryFn).not.toHaveBeenCalled();
  });

  it("revalidateAgainstDW updates the store with the fresh payload", async () => {
    const key = trpcQueryKey(["environmental", "getSoilSurvey"], { bbox: "0,0,1,1", zoom: 10 });
    const storedEntry: StoredLayerQueryEntry = {
      key: "swr-update",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now() - 1000,
      expiresAt: Date.now() + 60000,
      approxByteSize: 50,
      value: { type: "FeatureCollection", features: [] },
    };
    await setEntry(STORE_CONFIG, "swr-update", storedEntry);

    const freshData = { type: "FeatureCollection", features: [{ id: 99 }] };
    const queryFn = vi.fn().mockResolvedValue(freshData);

    const result = await revalidateAgainstDW(queryFn, FAKE_CONTEXT, key, "swr-update", storedEntry);

    expect(result).toEqual(freshData);
    const updated = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "swr-update");
    expect(updated?.value).toEqual(freshData);
  });

  it("throttles revalidation requests to max 2 concurrent requests", async () => {
    const key = trpcQueryKey(["environmental", "getWatersheds"], { bbox: "0,0,1,1" });
    const baseEntry: StoredLayerQueryEntry = {
      key: "swr-throttle",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now(),
      expiresAt: Date.now() + 60000,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [] },
    };

    let resolve1!: (val: unknown) => void;
    let resolve2!: (val: unknown) => void;
    let resolve3!: (val: unknown) => void;

    const p1Promise = new Promise((r) => { resolve1 = r; });
    const p2Promise = new Promise((r) => { resolve2 = r; });
    const p3Promise = new Promise((r) => { resolve3 = r; });

    const fn1 = () => p1Promise;
    const fn2 = () => p2Promise;
    const fn3 = () => p3Promise;

    const p1 = revalidateAgainstDW(fn1, FAKE_CONTEXT, key, "k1", baseEntry);
    const p2 = revalidateAgainstDW(fn2, FAKE_CONTEXT, key, "k2", baseEntry);
    const p3 = revalidateAgainstDW(fn3, FAKE_CONTEXT, key, "k3", baseEntry);

    expect(getActiveRevalidationsCount()).toBe(2);

    resolve1({ ok: 1 });
    await p1;

    expect(getActiveRevalidationsCount()).toBe(2);

    resolve2({ ok: 2 });
    await p2;

    resolve3({ ok: 3 });
    await p3;

    expect(getActiveRevalidationsCount()).toBe(0);
  });
});
