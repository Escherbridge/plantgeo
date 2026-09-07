/**
 * What the per-layer cache policy actually DOES to the IndexedDB cache: the TTL a nature earns,
 * the background revalidation a manual layer does not get, the fetch a manual refetch does
 * trigger, and a retention limit evicting for real -- asserted on what is left on disk, never on
 * a call count of the sweep.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryFunctionContext } from "@tanstack/react-query";
import { getAllEntryKeys, setEntry } from "@/lib/cache/indexeddb-store";
import { MANUAL_TTL_MS } from "@/lib/cache/layer-cache-policy";
import {
  layerCachePolicyFor,
  requestLayerRefresh,
  resetLayerCachePolicyForTests,
  useLayerCachePolicyStore,
} from "@/lib/cache/layer-cache-policy-store";
import {
  CACHE_SCHEMA_VERSION,
  HISTORICAL_TTL_MS,
  LIVE_TTL_MS,
  REVALIDATION_MIN_INTERVAL_MS,
  STORE_CONFIG,
  createIndexedDbLayerQueryPersister,
  enforceLayerRetention,
  resetCacheAccounting,
  resolveCacheTtlMs,
  type StoredLayerQueryEntry,
} from "@/lib/cache/query-persister";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "./fake-indexeddb";

const persister = createIndexedDbLayerQueryPersister(() => null);
const FAKE_CONTEXT = {} as QueryFunctionContext;
const SERVER_TODAY = "2026-08-14";

function trpcQueryKey(path: string[], input: Record<string, unknown>): readonly unknown[] {
  return [path, { input, type: "query" }];
}

function makeQuery(queryKey: readonly unknown[], queryHash: string) {
  return { queryKey, queryHash } as unknown as Parameters<typeof persister>[2];
}

function storedEntry(
  key: string,
  layerId: LayerToggleId,
  day: string,
  overrides: Partial<StoredLayerQueryEntry> = {}
): StoredLayerQueryEntry {
  const now = Date.now();
  return {
    key,
    schemaVersion: CACHE_SCHEMA_VERSION,
    createdAt: now - 5_000,
    expiresAt: now + 10 * 60_000,
    lastAccessedAt: now,
    approxByteSize: 100,
    layerId,
    day,
    value: { type: "FeatureCollection", features: [], availability: "published", reason: null },
    ...overrides,
  };
}

/** Polls until `predicate` holds; background revalidation settles over several event-loop turns. */
async function waitUntil(predicate: () => boolean, attempts = 60): Promise<void> {
  for (let index = 0; index < attempts; index += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  expect(predicate()).toBe(true);
}

const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();

beforeEach(() => {
  globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
  resetCacheAccounting();
  resetLayerCachePolicyForTests();
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
  resetLayerCachePolicyForTests();
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

describe("resolveCacheTtlMs under a per-layer policy", () => {
  it("keeps the pre-policy rule for a daily_series layer on automatic", () => {
    const historical = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const today = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: SERVER_TODAY,
    });
    const dateless = trpcQueryKey(["environmental", "getStreamflow"], { bbox: "0,0,1,1" });

    expect(layerCachePolicyFor("vegetation").refreshMode).toBe("automatic");
    expect(resolveCacheTtlMs(historical)).toBe(HISTORICAL_TTL_MS);
    expect(resolveCacheTtlMs(today)).toBe(LIVE_TTL_MS);
    expect(resolveCacheTtlMs(dateless)).toBe(LIVE_TTL_MS);
  });

  it("gives a manual-by-nature layer the long TTL for every day, live edge included", () => {
    // drought is `release_series`, so manual by default. A five-minute live TTL under "manual"
    // would still hit the network every five minutes and the setting would mean nothing.
    expect(layerCachePolicyFor("drought").refreshMode).toBe("manual");
    for (const date of ["2026-08-01", SERVER_TODAY, "2026-09-30"]) {
      const key = trpcQueryKey(["environmental", "getDroughtClassification"], {
        bbox: "0,0,1,1",
        date,
      });
      expect(resolveCacheTtlMs(key), date).toBe(MANUAL_TTL_MS);
    }
  });

  it("follows a user's override rather than the nature", () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: SERVER_TODAY,
    });
    expect(resolveCacheTtlMs(key)).toBe(LIVE_TTL_MS);

    useLayerCachePolicyStore.getState().setRefreshMode("vegetation", "manual");
    expect(resolveCacheTtlMs(key)).toBe(MANUAL_TTL_MS);

    // ...and a static_lookup layer the user pushed the other way goes back to the day rule.
    const watersheds = trpcQueryKey(["environmental", "getWatershedBoundaries"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    expect(resolveCacheTtlMs(watersheds)).toBe(MANUAL_TTL_MS);
    useLayerCachePolicyStore.getState().setRefreshMode("watersheds", "automatic");
    expect(resolveCacheTtlMs(watersheds)).toBe(HISTORICAL_TTL_MS);
  });

  it("leaves an unattributable key on the pre-policy rule -- the negative control", () => {
    const key = trpcQueryKey(["environmental", "getSoilProperties"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    expect(resolveCacheTtlMs(key)).toBe(HISTORICAL_TTL_MS);
  });
});

describe("manual refresh mode", () => {
  it("does not revalidate a manual layer in the background", async () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const query = makeQuery(key, "manual-hit");
    // Old enough that the throttle would let a revalidation through on any automatic layer.
    await setEntry(
      STORE_CONFIG,
      "manual-hit",
      storedEntry("manual-hit", "drought", "2026-08-01", {
        lastRevalidatedAt: Date.now() - 5 * REVALIDATION_MIN_INTERVAL_MS,
      })
    );

    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [{ id: 1 }] });
    const result = await persister(queryFn, FAKE_CONTEXT, query);
    await new Promise((resolve) => setTimeout(resolve, 40));

    expect((result as { features: unknown[] }).features).toEqual([]);
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("still revalidates a layer left on its automatic default -- the negative control", async () => {
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const query = makeQuery(key, "auto-hit");
    await setEntry(
      STORE_CONFIG,
      "auto-hit",
      storedEntry("auto-hit", "vegetation", "2026-08-01", {
        lastRevalidatedAt: Date.now() - 5 * REVALIDATION_MIN_INTERVAL_MS,
      })
    );

    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [{ id: 1 }] });
    await persister(queryFn, FAKE_CONTEXT, query);

    await waitUntil(() => queryFn.mock.calls.length > 0);
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("stops revalidating a daily_series layer the user switched to manual", async () => {
    useLayerCachePolicyStore.getState().setRefreshMode("vegetation", "manual");
    const key = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const query = makeQuery(key, "switched-to-manual");
    await setEntry(
      STORE_CONFIG,
      "switched-to-manual",
      storedEntry("switched-to-manual", "vegetation", "2026-08-01", {
        lastRevalidatedAt: Date.now() - 5 * REVALIDATION_MIN_INTERVAL_MS,
      })
    );

    const queryFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    await persister(queryFn, FAKE_CONTEXT, query);
    await new Promise((resolve) => setTimeout(resolve, 40));

    expect(queryFn).not.toHaveBeenCalled();
  });
});

describe("the manual refetch", () => {
  it("turns the next read of a manual layer into a real fetch", async () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const query = makeQuery(key, "refetch-me");
    await setEntry(
      STORE_CONFIG,
      "refetch-me",
      storedEntry("refetch-me", "drought", "2026-08-01", { createdAt: Date.now() - 60_000 })
    );

    const stale = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [{ id: "no" }] });
    const beforeRequest = await persister(stale, FAKE_CONTEXT, query);
    expect(stale).not.toHaveBeenCalled();
    expect((beforeRequest as { features: unknown[] }).features).toEqual([]);

    requestLayerRefresh("drought");

    const fresh = { type: "FeatureCollection", features: [{ id: "fresh" }], availability: "published" };
    const queryFn = vi.fn().mockResolvedValue(fresh);
    const afterRequest = await persister(queryFn, FAKE_CONTEXT, query);

    expect(queryFn).toHaveBeenCalledTimes(1);
    expect(afterRequest).toEqual(fresh);
  });

  it("supersedes only the requested layer, and only once", async () => {
    const droughtKey = trpcQueryKey(["environmental", "getDroughtClassification"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    const perimeterKey = trpcQueryKey(["environmental", "getFirePerimeters"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
    });
    await setEntry(
      STORE_CONFIG,
      "d1",
      storedEntry("d1", "drought", "2026-08-01", { createdAt: Date.now() - 60_000 })
    );
    await setEntry(
      STORE_CONFIG,
      "p1",
      storedEntry("p1", "fire-perimeters", "2026-08-01", { createdAt: Date.now() - 60_000 })
    );

    requestLayerRefresh("drought");

    const perimeterFn = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    await persister(perimeterFn, FAKE_CONTEXT, makeQuery(perimeterKey, "p1"));
    expect(perimeterFn, "a sibling layer was superseded too").not.toHaveBeenCalled();

    const first = vi
      .fn()
      .mockResolvedValue({ type: "FeatureCollection", features: [], availability: "published" });
    await persister(first, FAKE_CONTEXT, makeQuery(droughtKey, "d1"));
    expect(first).toHaveBeenCalledTimes(1);

    // The replacement was written after the request, so it is not superseded in turn -- a
    // refresh stamp that kept refusing would make the layer uncacheable for good.
    const second = vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] });
    await persister(second, FAKE_CONTEXT, makeQuery(droughtKey, "d1"));
    expect(second).not.toHaveBeenCalled();
  });
});

describe("retention limits", () => {
  async function seedDays(
    layerId: LayerToggleId,
    prefix: string,
    days: readonly string[],
    firstAccessedAt: number
  ): Promise<void> {
    for (const [index, day] of days.entries()) {
      const key = `${prefix}-${day}`;
      await setEntry(
        STORE_CONFIG,
        key,
        // Ascending, so `days[0]` is the least recently used.
        storedEntry(key, layerId, day, { lastAccessedAt: firstAccessedAt + index * 1_000 })
      );
    }
  }

  /** Polls disk until it holds `count` payloads; the write-path sweep is fire-and-forget. */
  async function settledKeys(count: number, attempts = 60): Promise<string[]> {
    let keys: string[] = [];
    for (let index = 0; index < attempts; index += 1) {
      keys = (await getAllEntryKeys(STORE_CONFIG)).sort();
      if (keys.length === count) return keys;
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    return keys;
  }

  it("evicts down to the limit, keeping the most recently USED days", async () => {
    const days = ["2000-11-01", "2004-06-02", "2011-03-03", "2020-07-04", "2026-08-05"];
    await seedDays("fire", "fire", days, Date.now() - 100_000);
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 2);

    const swept = await enforceLayerRetention("fire");

    expect(swept).not.toBeNull();
    expect(swept?.evictedDays).toBe(3);
    expect(swept?.retainedDays).toBe(2);

    // Asserted on what is LEFT on disk, not on the sweep having been called.
    const remaining = (await getAllEntryKeys(STORE_CONFIG)).sort();
    expect(remaining).toEqual(["fire-2020-07-04", "fire-2026-08-05"]);
  });

  it("drops every entry covering an evicted day, not just one of them", async () => {
    // One day accumulates an entry per viewport it was read at; keeping some of them would
    // leave a day the sync track lights but which mostly misses.
    for (const viewport of ["a", "b", "c"]) {
      const key = `multi-${viewport}`;
      await setEntry(
        STORE_CONFIG,
        key,
        storedEntry(key, "fire", "2001-01-01", { lastAccessedAt: Date.now() - 90_000 })
      );
    }
    await seedDays("fire", "keep", ["2026-08-05"], Date.now());
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 1);

    await enforceLayerRetention("fire");

    expect(await getAllEntryKeys(STORE_CONFIG)).toEqual(["keep-2026-08-05"]);
  });

  it("never touches another layer's days", async () => {
    await seedDays("fire", "fire", ["2001-01-01", "2002-01-01", "2003-01-01"], Date.now() - 30_000);
    await seedDays("vegetation", "veg", ["2001-01-01", "2002-01-01"], Date.now() - 30_000);
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 1);

    await enforceLayerRetention("fire");

    const remaining = (await getAllEntryKeys(STORE_CONFIG)).sort();
    expect(remaining).toEqual(["fire-2003-01-01", "veg-2001-01-01", "veg-2002-01-01"]);
  });

  it("does nothing when the layer holds fewer days than the limit", async () => {
    await seedDays("fire", "fire", ["2001-01-01", "2002-01-01"], Date.now() - 30_000);
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 5);

    const swept = await enforceLayerRetention("fire");

    expect(swept?.evictedDays).toBe(0);
    expect(swept?.retainedDays).toBe(2);
    expect((await getAllEntryKeys(STORE_CONFIG)).length).toBe(2);
  });

  it("refuses to run, rather than evicting, when the layer has no limit", async () => {
    await seedDays("watersheds", "ws", ["2001-01-01", "2002-01-01"], Date.now() - 30_000);
    expect(layerCachePolicyFor("watersheds").retainedDayLimit).toBeNull();

    // `null` is "the sweep did not run", which must not render as "swept, nothing to do".
    expect(await enforceLayerRetention("watersheds")).toBeNull();
    expect((await getAllEntryKeys(STORE_CONFIG)).length).toBe(2);
  });

  it("honours a user's unlimited override on a layer whose nature limits it", async () => {
    await seedDays("fire", "fire", ["2001-01-01", "2002-01-01", "2003-01-01"], Date.now() - 30_000);
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", null);

    expect(await enforceLayerRetention("fire")).toBeNull();
    expect((await getAllEntryKeys(STORE_CONFIG)).length).toBe(3);
  });

  it("enforces the limit on its own after a write lands a new day", async () => {
    await seedDays("fire", "fire", ["2001-01-01", "2002-01-01"], Date.now() - 30_000);
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 2);

    const key = trpcQueryKey(["wildfire", "getFireDetections"], {
      bbox: "0,0,1,1",
      date: "2026-08-05",
      zoom: 6,
    });
    const queryFn = vi.fn().mockResolvedValue({ state: "ready", data: { cells: [] } });
    await persister(queryFn, FAKE_CONTEXT, makeQuery(key, "fresh-fire-day"));

    // The sweep is fired and forgotten off the write path, so this polls rather than asserting
    // that the sweep was CALLED.
    expect(await settledKeys(2)).toEqual(["fire-2002-01-01", "fresh-fire-day"]);
  });
});

describe("the fire lane joins the allowlist", () => {
  it("caches a fire-detections answer and attributes it to the fire toggle", async () => {
    const key = trpcQueryKey(["wildfire", "getFireDetections"], {
      bbox: "0,0,1,1",
      date: "2026-08-01",
      zoom: 6,
    });
    const query = makeQuery(key, "fire-detections-hit");
    const payload = { state: "ready", data: { cells: [{ id: 1 }] } };
    const queryFn = vi.fn().mockResolvedValue(payload);

    await persister(queryFn, FAKE_CONTEXT, query);
    const second = vi.fn().mockResolvedValue({ state: "ready", data: { cells: [] } });
    const result = await persister(second, FAKE_CONTEXT, query);

    expect(second).not.toHaveBeenCalled();
    expect(result).toEqual(payload);
  });

  it("refuses to store a Parquet reader's transient upstream failure", async () => {
    const key = trpcQueryKey(["wildfire", "getFireDetections"], {
      bbox: "0,0,1,1",
      date: "2026-08-02",
      zoom: 6,
    });
    const query = makeQuery(key, "fire-detections-fault");
    const queryFn = vi.fn().mockResolvedValue({
      state: "upstream_unavailable",
      fault: { kind: "object_store", message: "503" },
    });

    await persister(queryFn, FAKE_CONTEXT, query);

    // A stored fault under a long TTL would read back as a settled warehouse answer.
    expect(await getAllEntryKeys(STORE_CONFIG)).not.toContain("fire-detections-fault");
  });
});
