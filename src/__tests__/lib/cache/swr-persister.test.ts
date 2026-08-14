import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryFunctionContext } from "@tanstack/react-query";
import { setEntry, getEntry } from "@/lib/cache/indexeddb-store";
import {
  CACHE_SCHEMA_VERSION,
  STORE_CONFIG,
  indexedDbLayerQueryPersister,
  revalidateAgainstDW,
  getActiveRevalidationsCount,
  type StoredLayerQueryEntry,
} from "@/lib/cache/query-persister";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "./fake-indexeddb";

function trpcQueryKey(path: string[], input?: Record<string, unknown>): readonly unknown[] {
  return input === undefined ? [path] : [path, { input, type: "query" }];
}

function makeQuery(queryKey: readonly unknown[], queryHash: string) {
  return { queryKey, queryHash } as unknown as Parameters<typeof indexedDbLayerQueryPersister>[2];
}

const FAKE_CONTEXT = {} as QueryFunctionContext;
const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();

describe("SWR IndexedDB DW Reconciliation & Throttling", () => {
  beforeEach(() => {
    globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
    useTimeSliderStore.setState({ capabilities: { serverCurrentDate: "2026-08-14", futureAxisDays: 0, layers: [] } });
  });

  afterEach(() => {
    globalThis.indexedDB = originalIndexedDb;
    useTimeSliderStore.setState(initialTimeSliderState, true);
  });

  it("serves stored entry instantly (0 ms) and dispatches background SWR revalidation", async () => {
    const key = trpcQueryKey(["environmental", "getClimateField"], { bbox: "0,0,1,1", date: "2026-08-01" });
    const query = makeQuery(key, "swr-instant-hit");
    const initialPayload = { type: "FeatureCollection", features: [{ id: 1 }], etag: "v1" };

    const storedEntry: StoredLayerQueryEntry = {
      key: "swr-instant-hit",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now() - 1000,
      expiresAt: Date.now() + 60000,
      lastAccessedAt: Date.now() - 1000,
      approxByteSize: 100,
      value: initialPayload,
      etag: "v1",
      dataRevision: "rev-1",
    };
    await setEntry(STORE_CONFIG, "swr-instant-hit", storedEntry);

    const revalidatedPayload = { type: "FeatureCollection", features: [{ id: 1 }, { id: 2 }], etag: "v2" };
    const queryFn = vi.fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(revalidatedPayload), 50))
    );

    // Call persister - must return initial payload immediately (0ms)
    const result = await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);
    expect(result).toEqual(initialPayload);

    // Wait for background revalidation to finish
    await new Promise((r) => setTimeout(r, 100));

    const updatedStored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "swr-instant-hit");
    expect(updatedStored?.value).toEqual(revalidatedPayload);
    expect(updatedStored?.etag).toBe("v2");
  });

  it("revalidateAgainstDW updates store with fresh payload on HTTP 200 equivalent", async () => {
    const key = trpcQueryKey(["environmental", "getSoilSurvey"], { bbox: "0,0,1,1", zoom: 10 });
    const storedEntry: StoredLayerQueryEntry = {
      key: "swr-update",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now() - 1000,
      expiresAt: Date.now() + 60000,
      lastAccessedAt: Date.now() - 1000,
      approxByteSize: 50,
      value: { type: "FeatureCollection", features: [] },
    };
    await setEntry(STORE_CONFIG, "swr-update", storedEntry);

    const freshData = { type: "FeatureCollection", features: [{ id: 99 }], etag: "v99", dataRevision: "rev-99" };
    const queryFn = vi.fn().mockResolvedValue(freshData);

    const result = await revalidateAgainstDW(queryFn, FAKE_CONTEXT, key, "swr-update", storedEntry);

    expect(result).toEqual(freshData);
    const updated = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "swr-update");
    expect(updated?.etag).toBe("v99");
    expect(updated?.dataRevision).toBe("rev-99");
  });

  it("throttles revalidation requests to max 2 concurrent requests", async () => {
    const key = trpcQueryKey(["environmental", "getWatersheds"], { bbox: "0,0,1,1" });
    const baseEntry: StoredLayerQueryEntry = {
      key: "swr-throttle",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now(),
      expiresAt: Date.now() + 60000,
      lastAccessedAt: Date.now(),
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
