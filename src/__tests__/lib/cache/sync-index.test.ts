/**
 * The synced-day index: attribution (including the two router paths that serve twelve layer
 * rows between them), enumeration, the per-layer reset, and the degradation contract.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import type { QueryFunctionContext } from "@tanstack/react-query";
import { getAllEntries, setEntry } from "@/lib/cache/indexeddb-store";
import {
  CACHEABLE_LAYER_QUERIES,
  CACHE_SCHEMA_VERSION,
  STORE_CONFIG,
  attributeQueryKey,
  clearLayerEntries,
  createIndexedDbLayerQueryPersister,
  resetCacheAccounting,
  scanSyncedDays,
  type StoredLayerQueryEntry,
} from "@/lib/cache/query-persister";
import {
  clearLayerSyncedDays,
  resetSyncIndexForTests,
  useLayerSyncedBytes,
  useSyncIndexReady,
  useSyncIndexStore,
  useSyncedDays,
} from "@/stores/sync-index-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "./fake-indexeddb";

function trpcQueryKey(path: string[], input: Record<string, unknown>): readonly unknown[] {
  return [path, { input, type: "query" }];
}

/** The IDB key react-query would mint for this query: `JSON.stringify` with sorted object keys. */
function cacheKeyFor(queryKey: readonly unknown[]): string {
  return JSON.stringify(queryKey);
}

function storedEntry(
  key: string,
  overrides: Partial<StoredLayerQueryEntry> = {}
): StoredLayerQueryEntry {
  const now = Date.now();
  return {
    key,
    schemaVersion: CACHE_SCHEMA_VERSION,
    createdAt: now,
    expiresAt: now + 60_000,
    lastAccessedAt: now,
    approxByteSize: 100,
    value: { type: "FeatureCollection", features: [] },
    ...overrides,
  };
}

const FAKE_CONTEXT = {} as QueryFunctionContext;
const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();

beforeEach(() => {
  globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
  resetCacheAccounting();
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
  // Unmount first: a component still subscribed when the next test resets the store would see
  // that reset as an update outside `act`.
  cleanup();
  globalThis.indexedDB = originalIndexedDb;
  resetSyncIndexForTests();
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

describe("attributeQueryKey", () => {
  it("splits one soil-field path across its three layer rows by `measure`", () => {
    const forMeasure = (measure: string) =>
      attributeQueryKey(
        trpcQueryKey(["environmental", "getSoilField"], { bbox: "0,0,1,1", measure })
      ).layerId;

    expect(forMeasure("moisture")).toBe("soil-moisture");
    expect(forMeasure("temperature")).toBe("soil-temperature");
    expect(forMeasure("vpd")).toBe("soil-vpd");
  });

  it("attributes a `measure`-less soil read to the measure the server resolves it to", () => {
    const attribution = attributeQueryKey(
      trpcQueryKey(["environmental", "getSoilField"], { bbox: "0,0,1,1" })
    );
    expect(attribution.layerId).toBe("soil-moisture");
  });

  it("splits one climate path across its nine signal rows by `signal`", () => {
    const forSignal = (signal: string) =>
      attributeQueryKey(
        trpcQueryKey(["environmental", "getClimateField"], { bbox: "0,0,1,1", signal })
      ).layerId;

    expect(forSignal("precipitation")).toBe("climate-precipitation");
    expect(forSignal("soil-wetness-profile")).toBe("climate-soil-wetness-profile");
    // No signal: the server resolves it to air-temperature, so that is the answer stored.
    expect(
      attributeQueryKey(trpcQueryKey(["environmental", "getClimateField"], { bbox: "0,0,1,1" }))
        .layerId
    ).toBe("climate-air-temperature");
  });

  it("attributes both water feeds to the one toggle that draws them", () => {
    const bbox = { bbox: "0,0,1,1" };
    expect(attributeQueryKey(trpcQueryKey(["environmental", "getStreamflow"], bbox)).layerId).toBe(
      "water"
    );
    expect(attributeQueryKey(trpcQueryKey(["environmental", "getGroundwater"], bbox)).layerId).toBe(
      "water"
    );
  });

  it("attributes every allowlisted path to a layer", () => {
    // The drift guard: a layer added to the allowlist with no attribution would be cached and
    // then be invisible on every sync track, with nothing anywhere reporting it.
    for (const routerPath of CACHEABLE_LAYER_QUERIES) {
      const attribution = attributeQueryKey(
        trpcQueryKey(routerPath.split("."), { bbox: "0,0,1,1" })
      );
      expect(attribution.layerId, `${routerPath} has no layer attribution`).not.toBeNull();
    }
  });

  it("reads the day from the input, and leaves a dateless read with no day", () => {
    expect(
      attributeQueryKey(
        trpcQueryKey(["environmental", "getVegetationIndex"], {
          bbox: "0,0,1,1",
          date: "2026-08-01",
        })
      ).day
    ).toBe("2026-08-01");
    expect(
      attributeQueryKey(trpcQueryKey(["environmental", "getWatersheds"], { bbox: "0,0,1,1" })).day
    ).toBeUndefined();
  });
});

describe("stamping and enumeration", () => {
  it("stamps the layer and day onto an entry the persister writes", async () => {
    const key = trpcQueryKey(["environmental", "getSoilField"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
      measure: "temperature",
    });
    const persister = createIndexedDbLayerQueryPersister(() => null);
    await persister(
      vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }),
      FAKE_CONTEXT,
      { queryKey: key, queryHash: cacheKeyFor(key) } as unknown as Parameters<typeof persister>[2]
    );

    const [entry] = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(entry.layerId).toBe("soil-temperature");
    expect(entry.day).toBe("2026-08-01");
  });

  it("recovers layer and day for an unstamped legacy entry by parsing its key", async () => {
    // Written before the stamp existed: react-query's hash is JSON.stringify of the query key,
    // so the attribution is still recoverable and the entry does not have to be discarded.
    const key = trpcQueryKey(["environmental", "getSoilField"], {
      bbox: "0,0,1,1",
      date: "2026-07-04",
      measure: "vpd",
    });
    const cacheKey = cacheKeyFor(key);
    await setEntry(STORE_CONFIG, cacheKey, storedEntry(cacheKey));

    const scan = await scanSyncedDays();
    expect([...(scan?.index.get("soil-vpd")?.keys() ?? [])]).toEqual(["2026-07-04"]);
  });

  it("never counts an expired entry as synced, and sweeps it while walking past", async () => {
    const freshKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getVegetationIndex"], {
        bbox: "0,0,1,1",
        date: "2026-08-02",
      })
    );
    const expiredKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getVegetationIndex"], {
        bbox: "0,0,1,1",
        date: "2026-08-03",
      })
    );
    await setEntry(STORE_CONFIG, freshKey, storedEntry(freshKey));
    await setEntry(
      STORE_CONFIG,
      expiredKey,
      storedEntry(expiredKey, { expiresAt: Date.now() - 1 })
    );

    const scan = await scanSyncedDays();
    expect([...(scan?.index.get("vegetation")?.keys() ?? [])]).toEqual(["2026-08-02"]);

    const remaining = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(remaining.map((entry) => entry.key)).toEqual([freshKey]);
  });

  it("leaves a dateless entry out of the index entirely", async () => {
    const cacheKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getWatersheds"], { bbox: "0,0,1,1" })
    );
    await setEntry(STORE_CONFIG, cacheKey, storedEntry(cacheKey));

    const scan = await scanSyncedDays();
    // It answers for the live edge, which is not a day any slider track can point at.
    expect(scan?.index.has("watersheds")).toBe(false);
  });

  it("clears one layer's entries and leaves every other layer alone", async () => {
    const soilKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getSoilField"], {
        bbox: "0,0,1,1",
        date: "2026-08-01",
        measure: "moisture",
      })
    );
    const vegetationKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getVegetationIndex"], {
        bbox: "0,0,1,1",
        date: "2026-08-01",
      })
    );
    await setEntry(STORE_CONFIG, soilKey, storedEntry(soilKey));
    await setEntry(STORE_CONFIG, vegetationKey, storedEntry(vegetationKey));

    const index = await clearLayerEntries("soil-moisture");

    expect(index?.has("soil-moisture")).toBe(false);
    expect([...(index?.get("vegetation")?.keys() ?? [])]).toEqual(["2026-08-01"]);
    const remaining = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(remaining.map((entry) => entry.key)).toEqual([vegetationKey]);
  });

  it("reports an unreadable store as `null`, never as an empty index", async () => {
    // @ts-expect-error -- simulate SSR/jsdom/private-mode absence explicitly.
    delete globalThis.indexedDB;
    await expect(scanSyncedDays()).resolves.toBeNull();
    await expect(clearLayerEntries("vegetation")).resolves.toBeNull();
  });
});

describe("useSyncIndexStore", () => {
  it("hydrates once from disk and reports the days it found", async () => {
    const cacheKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getSoilField"], {
        bbox: "0,0,1,1",
        date: "2026-08-01",
        measure: "moisture",
      })
    );
    await setEntry(STORE_CONFIG, cacheKey, storedEntry(cacheKey, { approxByteSize: 4096 }));

    await useSyncIndexStore.getState().hydrate();

    expect(renderHook(() => useSyncIndexReady()).result.current).toBe(true);
    expect([...renderHook(() => useSyncedDays("soil-moisture")).result.current]).toEqual([
      "2026-08-01",
    ]);
    expect(renderHook(() => useLayerSyncedBytes("soil-moisture")).result.current).toBe(4096);
  });

  it("stays not-ready, and empty, when IndexedDB cannot be read at all", async () => {
    // @ts-expect-error -- simulate SSR/jsdom/private-mode absence explicitly.
    delete globalThis.indexedDB;
    await useSyncIndexStore.getState().hydrate();

    expect(renderHook(() => useSyncIndexReady()).result.current).toBe(false);
    expect(renderHook(() => useSyncedDays("vegetation")).result.current.size).toBe(0);
  });

  it("folds a persister write into the index without another disk scan", async () => {
    await useSyncIndexStore.getState().hydrate();
    expect(useSyncIndexStore.getState().byLayer.size).toBe(0);

    const key = trpcQueryKey(["environmental", "getClimateField"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
      signal: "precipitation",
    });
    const persister = createIndexedDbLayerQueryPersister(() => null);
    await persister(
      vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }),
      FAKE_CONTEXT,
      { queryKey: key, queryHash: cacheKeyFor(key) } as unknown as Parameters<typeof persister>[2]
    );

    expect([
      ...(useSyncIndexStore.getState().byLayer.get("climate-precipitation")?.days ?? []),
    ]).toEqual(["2026-08-01"]);
  });

  it("keeps one layer's day set referentially stable when another layer changes", async () => {
    const vegetationKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getVegetationIndex"], {
        bbox: "0,0,1,1",
        date: "2026-08-01",
      })
    );
    await setEntry(STORE_CONFIG, vegetationKey, storedEntry(vegetationKey));
    await useSyncIndexStore.getState().hydrate();

    const before = useSyncIndexStore.getState().byLayer.get("vegetation")?.days;

    const key = trpcQueryKey(["environmental", "getStreamflow"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const persister = createIndexedDbLayerQueryPersister(() => null);
    await persister(
      vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }),
      FAKE_CONTEXT,
      { queryKey: key, queryHash: cacheKeyFor(key) } as unknown as Parameters<typeof persister>[2]
    );

    // The slider re-renders per scrub tick; a new Set here would re-run its geometry memo.
    expect(useSyncIndexStore.getState().byLayer.get("vegetation")?.days).toBe(before);
  });

  it("drops a layer's days when its timeline is reset", async () => {
    const cacheKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getVegetationIndex"], {
        bbox: "0,0,1,1",
        date: "2026-08-01",
      })
    );
    await setEntry(STORE_CONFIG, cacheKey, storedEntry(cacheKey));
    await useSyncIndexStore.getState().hydrate();
    expect(useSyncIndexStore.getState().byLayer.get("vegetation")?.days.size).toBe(1);

    await clearLayerSyncedDays("vegetation");

    expect(useSyncIndexStore.getState().byLayer.has("vegetation")).toBe(false);
    await expect(getAllEntries(STORE_CONFIG)).resolves.toEqual([]);
  });

  it("does not inflate a layer's byte total when an expired entry is replaced", async () => {
    // The scrub-pan cycle: an entry expires, the persister deletes it on the next read and
    // refetches. Counting the new entry without discounting the deleted one grew the number
    // rendered on the destructive "clear N saved days (X KB)" control on every cycle.
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const cacheKey = cacheKeyFor(key);
    await setEntry(
      STORE_CONFIG,
      cacheKey,
      storedEntry(cacheKey, { approxByteSize: 5_000, expiresAt: Date.now() + 15 })
    );
    await useSyncIndexStore.getState().hydrate();
    expect(useSyncIndexStore.getState().byLayer.get("vegetation")?.approxByteSize).toBe(5_000);

    await new Promise((resolve) => setTimeout(resolve, 30));
    const persister = createIndexedDbLayerQueryPersister(() => null);
    const payload = { type: "FeatureCollection", features: [] };
    await persister(vi.fn().mockResolvedValue(payload), FAKE_CONTEXT, {
      queryKey: key,
      queryHash: cacheKey,
    } as unknown as Parameters<typeof persister>[2]);

    const layer = useSyncIndexStore.getState().byLayer.get("vegetation");
    // Exactly one entry on disk, so exactly one entry's worth of bytes -- not 5,000 plus it.
    expect(layer?.days.size).toBe(1);
    expect(layer?.approxByteSize).toBeLessThan(5_000);
    expect(layer?.approxByteSize).toBeGreaterThan(0);
  });

  it("counts one day once when a write races the hydration scan", async () => {
    // The replay buffer exists so a write landing mid-scan is not lost; tagging it by cache key
    // is what stops a write the scan ALREADY saw from being counted a second time.
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,3,3",
      date: "2026-08-01",
    });
    const cacheKey = cacheKeyFor(key);
    await setEntry(STORE_CONFIG, cacheKey, storedEntry(cacheKey, { approxByteSize: 1_000 }));

    const hydrating = useSyncIndexStore.getState().hydrate();
    // Lands while the scan is in flight, for a key the scan is already reading.
    const persister = createIndexedDbLayerQueryPersister(() => null);
    await persister(vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }), FAKE_CONTEXT, {
      queryKey: key,
      queryHash: cacheKey,
    } as unknown as Parameters<typeof persister>[2]);
    await hydrating;

    expect(useSyncIndexStore.getState().byLayer.get("vegetation")?.days.size).toBe(1);
  });

  it("prunes a day whose entry has passed its TTL", async () => {
    const cacheKey = cacheKeyFor(
      trpcQueryKey(["environmental", "getVegetationIndex"], {
        bbox: "0,0,1,1",
        date: "2026-08-01",
      })
    );
    await setEntry(STORE_CONFIG, cacheKey, storedEntry(cacheKey, { expiresAt: Date.now() + 20 }));
    await useSyncIndexStore.getState().hydrate();
    expect(useSyncIndexStore.getState().byLayer.get("vegetation")?.days.size).toBe(1);

    await new Promise((resolve) => setTimeout(resolve, 40));
    useSyncIndexStore.getState().pruneExpired();

    expect(useSyncIndexStore.getState().byLayer.has("vegetation")).toBe(false);
  });
});
