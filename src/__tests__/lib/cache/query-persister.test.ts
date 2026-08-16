import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryFunctionContext } from "@tanstack/react-query";
import { getAllEntries, getEntry, setEntry } from "@/lib/cache/indexeddb-store";
import {
  CACHE_SCHEMA_VERSION,
  HISTORICAL_TTL_MS,
  LIVE_TTL_MS,
  STORE_CONFIG,
  indexedDbLayerQueryPersister,
  isPersistableQueryKey,
  resolveCacheTtlMs,
  type StoredLayerQueryEntry,
} from "@/lib/cache/query-persister";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "./fake-indexeddb";

/** Mirrors `@trpc/react-query`'s `getQueryKeyInternal` shape for a `.useQuery(input)` call. */
function trpcQueryKey(path: string[], input?: Record<string, unknown>): readonly unknown[] {
  return input === undefined ? [path] : [path, { input, type: "query" }];
}

/** The persister only reads `.queryKey` and `.queryHash`; nothing else about `Query` matters here. */
function makeQuery(queryKey: readonly unknown[], queryHash: string) {
  return { queryKey, queryHash } as unknown as Parameters<typeof indexedDbLayerQueryPersister>[2];
}

const FAKE_CONTEXT = {} as QueryFunctionContext;

const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();

beforeEach(() => {
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

afterEach(() => {
  globalThis.indexedDB = originalIndexedDb;
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

describe("isPersistableQueryKey", () => {
  it("accepts an allowlisted procedure carrying a bbox", () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], { bbox: "0,0,1,1" });
    expect(isPersistableQueryKey(key)).toBe(true);
  });

  it("accepts an allowlisted procedure carrying only a date", () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { date: "2026-01-01" });
    expect(isPersistableQueryKey(key)).toBe(true);
  });

  it("rejects a procedure that is not on the allowlist", () => {
    const key = trpcQueryKey(["environmental", "getSoilProperties"], { bbox: "0,0,1,1" });
    expect(isPersistableQueryKey(key)).toBe(false);
  });

  it("rejects an allowlisted procedure whose input has neither bbox nor date", () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], {});
    expect(isPersistableQueryKey(key)).toBe(false);
  });

  it("rejects a query with no input at all", () => {
    expect(isPersistableQueryKey(trpcQueryKey(["environmental", "getDroughtClassification"]))).toBe(
      false
    );
  });

  it("rejects a query key that isn't shaped like a tRPC one", () => {
    expect(isPersistableQueryKey([])).toBe(false);
    expect(isPersistableQueryKey([[], { input: { bbox: "0,0,1,1" } }])).toBe(false);
  });

  it("never opts in a procedure outside the geospatial allowlist, even with a bbox-shaped input", () => {
    const key = trpcQueryKey(["auth", "getSession"], { bbox: "0,0,1,1" });
    expect(isPersistableQueryKey(key)).toBe(false);
  });
});

describe("resolveCacheTtlMs", () => {
  it("gives a long TTL to a date strictly before the server's current date", () => {
    useTimeSliderStore.setState({ capabilities: { serverCurrentDate: "2026-08-05", futureAxisDays: 0, layers: [], streamsUnavailable: false } });
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { date: "2026-08-01" });
    expect(resolveCacheTtlMs(key)).toBe(HISTORICAL_TTL_MS);
  });

  it("gives a short TTL to the server's current date itself", () => {
    useTimeSliderStore.setState({ capabilities: { serverCurrentDate: "2026-08-05", futureAxisDays: 0, layers: [], streamsUnavailable: false } });
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { date: "2026-08-05" });
    expect(resolveCacheTtlMs(key)).toBe(LIVE_TTL_MS);
  });

  it("gives a short TTL to a future forecast date", () => {
    useTimeSliderStore.setState({ capabilities: { serverCurrentDate: "2026-08-05", futureAxisDays: 0, layers: [], streamsUnavailable: false } });
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { date: "2026-08-09" });
    expect(resolveCacheTtlMs(key)).toBe(LIVE_TTL_MS);
  });

  it("gives a short TTL to a query that carries no date at all", () => {
    useTimeSliderStore.setState({ capabilities: { serverCurrentDate: "2026-08-05", futureAxisDays: 0, layers: [], streamsUnavailable: false } });
    const key = trpcQueryKey(["environmental", "getStreamflow"], { bbox: "0,0,1,1" });
    expect(resolveCacheTtlMs(key)).toBe(LIVE_TTL_MS);
  });

  it("never invents today from the browser clock: an unknown server date is always short-TTL", () => {
    useTimeSliderStore.setState({ capabilities: null });
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { date: "2000-01-01" });
    expect(resolveCacheTtlMs(key)).toBe(LIVE_TTL_MS);
  });
});

describe("indexedDbLayerQueryPersister", () => {
  beforeEach(() => {
    globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
    useTimeSliderStore.setState({ capabilities: { serverCurrentDate: "2026-08-05", futureAxisDays: 0, layers: [], streamsUnavailable: false } });
  });

  it("on a miss, calls queryFn once and persists the result", async () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const query = makeQuery(key, "hash-miss");
    const payload = { type: "FeatureCollection", features: [], availability: "published", reason: null };
    const queryFn = vi.fn().mockResolvedValue(payload);

    const result = await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    expect(result).toEqual(payload);
    expect(queryFn).toHaveBeenCalledTimes(1);
    const stored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "hash-miss");
    expect(stored?.value).toEqual(payload);
    expect(stored?.schemaVersion).toBe(CACHE_SCHEMA_VERSION);
  });

  it("on a hit, returns the stored value immediately (0ms latency)", async () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const query = makeQuery(key, "hash-hit");
    const payload = {
      type: "FeatureCollection",
      features: [{ type: "Feature" }],
      availability: "published",
      reason: null,
    };
    const firstQueryFn = vi.fn().mockResolvedValue(payload);
    await indexedDbLayerQueryPersister(firstQueryFn, FAKE_CONTEXT, query);

    const secondQueryFn = vi.fn().mockResolvedValue({
      type: "FeatureCollection",
      features: [],
      availability: "published",
      reason: null,
    });
    const second = await indexedDbLayerQueryPersister(secondQueryFn, FAKE_CONTEXT, query);

    // Serves stored payload immediately from IndexedDB
    expect(second).toEqual(payload);
  });

  it("passes queries outside the allowlist straight through, uncached", async () => {
    const key = trpcQueryKey(["environmental", "getSoilProperties"], { lat: 1, lon: 2 });
    const query = makeQuery(key, "hash-not-allowlisted");
    const queryFn = vi.fn().mockResolvedValue({ some: "value" });

    await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);
    await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    expect(queryFn).toHaveBeenCalledTimes(2);
    await expect(getEntry(STORE_CONFIG, "hash-not-allowlisted")).resolves.toBeNull();
  });

  it("treats an expired entry as a miss and refreshes it", async () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], {
      date: "2020-01-01",
      bbox: "0,0,1,1",
    });
    const query = makeQuery(key, "hash-expired");
    const staleEntry: StoredLayerQueryEntry = {
      key: "hash-expired",
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now() - 1_000,
      expiresAt: Date.now() - 1, // already expired
      lastAccessedAt: Date.now() - 1_000,
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [], availability: "unavailable", reason: "old" },
    };
    await setEntry(STORE_CONFIG, "hash-expired", staleEntry);

    const freshPayload = {
      type: "FeatureCollection",
      features: [],
      availability: "published",
      reason: null,
    };
    const queryFn = vi.fn().mockResolvedValue(freshPayload);
    const result = await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    expect(queryFn).toHaveBeenCalledTimes(1);
    expect(result).toEqual(freshPayload);
    const stored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "hash-expired");
    expect(stored?.value).toEqual(freshPayload);
  });

  it("treats a schema-version mismatch as a miss even when the TTL has not elapsed", async () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2020-01-01",
    });
    const query = makeQuery(key, "hash-old-schema");
    const oldSchemaEntry: StoredLayerQueryEntry = {
      key: "hash-old-schema",
      schemaVersion: CACHE_SCHEMA_VERSION - 1,
      createdAt: Date.now(),
      expiresAt: Date.now() + HISTORICAL_TTL_MS, // deliberately far from expiring
      lastAccessedAt: Date.now(),
      approxByteSize: 10,
      value: { type: "FeatureCollection", features: [], availability: "published", reason: null },
    };
    await setEntry(STORE_CONFIG, "hash-old-schema", oldSchemaEntry);

    const freshPayload = {
      type: "FeatureCollection",
      features: [{ type: "Feature" }],
      availability: "published",
      reason: null,
    };
    const queryFn = vi.fn().mockResolvedValue(freshPayload);
    const result = await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    expect(queryFn).toHaveBeenCalledTimes(1);
    expect(result).toEqual(freshPayload);
    const stored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "hash-old-schema");
    expect(stored?.schemaVersion).toBe(CACHE_SCHEMA_VERSION);
  });

  it("never caches a response that represents a failed request", async () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { bbox: "0,0,1,1" });
    const query = makeQuery(key, "hash-failed");
    const failurePayload = {
      type: "FeatureCollection",
      features: [],
      availability: "request_failed",
      reason: null,
    };
    const queryFn = vi.fn().mockResolvedValue(failurePayload);

    const result = await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    expect(result).toEqual(failurePayload);
    await expect(getEntry(STORE_CONFIG, "hash-failed")).resolves.toBeNull();
  });

  it("propagates a queryFn rejection untouched and caches nothing", async () => {
    const key = trpcQueryKey(["environmental", "getStreamflow"], { bbox: "0,0,1,1" });
    const query = makeQuery(key, "hash-rejected");
    const queryFn = vi.fn().mockRejectedValue(new Error("network down"));

    await expect(indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query)).rejects.toThrow(
      "network down"
    );
    await expect(getEntry(STORE_CONFIG, "hash-rejected")).resolves.toBeNull();
  });

  it("a legitimately empty, non-failed result is still cached (a real gap is not a failure)", async () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2020-01-01",
    });
    const query = makeQuery(key, "hash-empty-but-real");
    const emptyButPublished = {
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      reason: "stale",
    };
    const queryFn = vi.fn().mockResolvedValue(emptyButPublished);

    await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    const stored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "hash-empty-but-real");
    expect(stored?.value).toEqual(emptyButPublished);
  });

  it("evicts the least-recently-used entries once the size budget would be exceeded", async () => {
    // Five 20 MB entries (declared directly, not via real payload size) total 100 MB --
    // double the 50 MB MAX_TOTAL_CACHE_BYTES budget -- so writing one more entry must evict
    // the oldest ones by `lastAccessedAt` until the total fits again.
    const bigEntry = (key: string, lastAccessedAt: number): StoredLayerQueryEntry => ({
      key,
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: lastAccessedAt,
      expiresAt: Date.now() + HISTORICAL_TTL_MS,
      lastAccessedAt,
      approxByteSize: 20 * 1024 * 1024,
      value: { tiny: true },
    });
    for (let i = 0; i < 5; i += 1) {
      await setEntry(STORE_CONFIG, `old-${i}`, bigEntry(`old-${i}`, i));
    }

    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2020-01-01",
    });
    const query = makeQuery(key, "new-arrival");
    const queryFn = vi.fn().mockResolvedValue({
      type: "FeatureCollection",
      features: [],
      availability: "published",
      reason: null,
    });

    await indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query);

    const remaining = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    const remainingKeys = remaining.map((entry) => entry.key).sort();
    // The three oldest (old-0..old-2) are evicted; the two most recently used (old-3, old-4)
    // plus the just-written entry survive.
    expect(remainingKeys).toEqual(["new-arrival", "old-3", "old-4"]);
  });

  it("passes straight through to queryFn when IndexedDB is unavailable", async () => {
    // @ts-expect-error -- simulate SSR/jsdom/private-mode absence explicitly.
    delete globalThis.indexedDB;
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], { bbox: "0,0,1,1" });
    const query = makeQuery(key, "hash-no-idb");
    const payload = { type: "FeatureCollection", features: [], availability: "published", reason: null };
    const queryFn = vi.fn().mockResolvedValue(payload);

    await expect(indexedDbLayerQueryPersister(queryFn, FAKE_CONTEXT, query)).resolves.toEqual(
      payload
    );
    expect(queryFn).toHaveBeenCalledTimes(1);
  });
});
