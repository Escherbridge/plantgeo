/**
 * The per-layer cache POLICY: what each nature implies by default, how a user override composes
 * over it, and that the override is still there after a reload -- asserted against the real
 * localStorage blob and a genuinely re-imported store, not a mock that hands back whatever the
 * test put in.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_REFRESH_MODE,
  DEFAULT_RETAINED_DAY_LIMIT,
  MAX_RETAINED_DAY_LIMIT,
  MIN_RETAINED_DAY_LIMIT,
  clampRetainedDayLimit,
  defaultLayerCachePolicy,
  layerCacheNature,
  layerCacheNatureTable,
  sanitizeLayerCacheOverrides,
  sanitizeLayerTimestamps,
  type LayerCacheNature,
} from "@/lib/cache/layer-cache-policy";
import {
  LAYER_CACHE_POLICY_PERSIST_KEY,
  isSupersededByRefreshRequest,
  layerCachePolicyFor,
  requestLayerRefresh,
  resetLayerCachePolicyForTests,
  useLayerCachePolicyStore,
} from "@/lib/cache/layer-cache-policy-store";
import { LAYER_TOGGLE_IDS, toggleIdForWarehouseLayerName } from "@/lib/map/layer-registry";

beforeEach(() => {
  resetLayerCachePolicyForTests();
});

afterEach(() => {
  resetLayerCachePolicyForTests();
  vi.resetModules();
});

describe("nature-derived defaults", () => {
  it("gives a static_lookup layer manual refresh and no day limit", () => {
    // watersheds has published exactly ONE version in its entire history; polling it is a
    // request a minute for a byte-identical answer, and a day-count limit governs nothing
    // because its reads carry no date at all.
    expect(layerCacheNature("watersheds")).toBe("static_lookup");
    const policy = layerCachePolicyFor("watersheds");
    expect(policy.refreshMode).toBe("manual");
    expect(policy.retainedDayLimit).toBeNull();
    expect(policy.isOverridden).toBe(false);
  });

  it("gives a release_series layer manual refresh and a finite day limit", () => {
    // burn-severity: five releases across 2015-2026.
    expect(layerCacheNature("burn-severity")).toBe("release_series");
    const policy = layerCachePolicyFor("burn-severity");
    expect(policy.refreshMode).toBe("manual");
    expect(policy.retainedDayLimit).toBe(DEFAULT_RETAINED_DAY_LIMIT.release_series);
    expect(policy.retainedDayLimit).toBe(180);
  });

  it("keeps a daily_series layer on automatic revalidation", () => {
    // The one nature whose past days really do get republished behind the reader, so background
    // revalidation is the correction path and must not default off.
    expect(layerCacheNature("fire")).toBe("daily_series");
    const policy = layerCachePolicyFor("fire");
    expect(policy.refreshMode).toBe("automatic");
    expect(policy.retainedDayLimit).toBe(90);
  });

  it("does not hand every layer the same policy", () => {
    const modes = new Set(LAYER_TOGGLE_IDS.map((id) => layerCachePolicyFor(id).refreshMode));
    expect(modes).toEqual(new Set(["manual", "automatic"]));
  });

  it("treats an unattributable query's layer as daily_series/automatic", () => {
    // The negative control that keeps a router path this cache cannot map from quietly
    // acquiring a year-long TTL: `null` gets exactly the pre-policy behaviour.
    const policy = layerCachePolicyFor(null);
    expect(policy.nature).toBe("daily_series");
    expect(policy.refreshMode).toBe("automatic");
    expect(policy.isOverridden).toBe(false);
  });

  it("declares a nature for every registry layer", () => {
    const table = layerCacheNatureTable();
    for (const toggleId of LAYER_TOGGLE_IDS) {
      expect(table[toggleId], `${toggleId} has no declared nature`).toBeDefined();
    }
    expect(Object.keys(table).sort()).toEqual([...LAYER_TOGGLE_IDS].sort());
  });

  it("states the same default for a nature as the table it is derived from", () => {
    for (const nature of ["static_lookup", "daily_series", "release_series"] as LayerCacheNature[]) {
      const policy = defaultLayerCachePolicy(nature);
      expect(policy.refreshMode).toBe(DEFAULT_REFRESH_MODE[nature]);
      expect(policy.retainedDayLimit).toBe(DEFAULT_RETAINED_DAY_LIMIT[nature]);
    }
  });
});

describe("agreement with the server's parquet lane contract", () => {
  /**
   * Read as TEXT rather than imported: `parquet-slider-capabilities.ts` pulls the DuckDB plane
   * client in, which has no business in a jsdom bundle. Names built at runtime
   * (`SLIDER_STREAM_LAYER_NAMES.drought`, `climateFieldStreamName(...)`) do not appear as string
   * literals and are skipped -- every nature that is NOT `daily_series` is a literal row, which
   * is exactly the set worth guarding.
   */
  function serverNaturesByLayerName(): Map<string, string> {
    // Resolved from the vitest root rather than `import.meta.url`: under jsdom that URL is not a
    // `file:` one and `fileURLToPath` rejects it.
    const source = readFileSync(
      resolve(process.cwd(), "src/lib/server/services/parquet-slider-capabilities.ts"),
      "utf8"
    );
    const found = new Map<string, string>();
    for (const chunk of source.split(/\blayerName:\s*/g).slice(1)) {
      const name = /^"([^"]+)"/.exec(chunk);
      if (name === null) continue;
      const nature = /\bparquetNature:\s*"([a-z_]+)"/.exec(chunk);
      if (nature === null) continue;
      found.set(name[1], nature[1]);
    }
    return found;
  }

  it("carries the server's parquetNature for every lane it can map to a toggle", () => {
    const server = serverNaturesByLayerName();
    // The extraction failing open would make this whole guard a green no-op.
    expect(server.size, "extracted no lane contracts at all").toBeGreaterThan(5);

    const table = layerCacheNatureTable();
    const compared: string[] = [];
    for (const [layerName, nature] of server) {
      const toggleId = toggleIdForWarehouseLayerName(layerName);
      if (toggleId === null) continue;
      compared.push(layerName);
      expect(table[toggleId], `${layerName} -> ${toggleId}`).toBe(nature);
    }
    expect(compared.length, `only mapped ${compared.join(", ")}`).toBeGreaterThanOrEqual(9);
  });
});

describe("user overrides", () => {
  it("composes an override over the nature default and marks the layer overridden", () => {
    useLayerCachePolicyStore.getState().setRefreshMode("fire", "manual");
    const policy = layerCachePolicyFor("fire");
    expect(policy.refreshMode).toBe("manual");
    // The knob not touched still follows the nature.
    expect(policy.retainedDayLimit).toBe(DEFAULT_RETAINED_DAY_LIMIT.daily_series);
    expect(policy.isOverridden).toBe(true);
  });

  it("leaves every other layer on its nature default -- the negative control", () => {
    useLayerCachePolicyStore.getState().setRefreshMode("fire", "manual");
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 5);

    const untouched = layerCachePolicyFor("vegetation");
    expect(untouched.refreshMode).toBe("automatic");
    expect(untouched.retainedDayLimit).toBe(90);
    expect(untouched.isOverridden).toBe(false);
  });

  it("lets a user ask for unlimited retention on a layer whose nature limits it", () => {
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", null);
    const policy = layerCachePolicyFor("fire");
    expect(policy.retainedDayLimit).toBeNull();
    expect(policy.isOverridden).toBe(true);
  });

  it("returns a layer to its nature default on reset", () => {
    useLayerCachePolicyStore.getState().setRefreshMode("watersheds", "automatic");
    expect(layerCachePolicyFor("watersheds").refreshMode).toBe("automatic");
    useLayerCachePolicyStore.getState().resetPolicy("watersheds");
    expect(layerCachePolicyFor("watersheds").refreshMode).toBe("manual");
    expect(layerCachePolicyFor("watersheds").isOverridden).toBe(false);
  });

  it("clamps a limit into the usable band rather than storing it as given", () => {
    // 0 is not a limit, it is a disabled cache, and it would evict on every write.
    expect(clampRetainedDayLimit(0)).toBe(MIN_RETAINED_DAY_LIMIT);
    expect(clampRetainedDayLimit(-3)).toBe(MIN_RETAINED_DAY_LIMIT);
    expect(clampRetainedDayLimit(7.9)).toBe(7);
    expect(clampRetainedDayLimit(9e9)).toBe(MAX_RETAINED_DAY_LIMIT);
    expect(clampRetainedDayLimit(Number.NaN)).toBeNull();
    expect(clampRetainedDayLimit(null)).toBeNull();

    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 0);
    expect(layerCachePolicyFor("fire").retainedDayLimit).toBe(MIN_RETAINED_DAY_LIMIT);
  });
});

describe("persistence", () => {
  it("keeps a user override across a reload, read back through the real persisted blob", async () => {
    useLayerCachePolicyStore.getState().setRefreshMode("fire", "manual");
    useLayerCachePolicyStore.getState().setRetainedDayLimit("fire", 7);

    const raw = globalThis.localStorage.getItem(LAYER_CACHE_POLICY_PERSIST_KEY);
    expect(raw, "nothing reached localStorage").not.toBeNull();
    expect(raw).toContain("\"fire\"");

    // A real reload: the module graph is dropped and the store is constructed again, hydrating
    // itself from the blob above. Nothing in this test hands the reloaded store its answer.
    vi.resetModules();
    const reloaded = await import("@/lib/cache/layer-cache-policy-store");

    const policy = reloaded.layerCachePolicyFor("fire");
    expect(policy.refreshMode).toBe("manual");
    expect(policy.retainedDayLimit).toBe(7);
    expect(policy.isOverridden).toBe(true);

    // Same reloaded store, a layer the user never touched: still on its nature default.
    const untouched = reloaded.layerCachePolicyFor("vegetation");
    expect(untouched.refreshMode).toBe("automatic");
    expect(untouched.isOverridden).toBe(false);

    reloaded.resetLayerCachePolicyForTests();
  });

  it("keeps the last-fetched stamp across a reload", async () => {
    useLayerCachePolicyStore.getState().recordFetched("drought", 1_700_000_000_000);

    vi.resetModules();
    const reloaded = await import("@/lib/cache/layer-cache-policy-store");
    expect(reloaded.useLayerCachePolicyStore.getState().lastFetchedAt.drought).toBe(
      1_700_000_000_000
    );
    reloaded.resetLayerCachePolicyForTests();
  });

  it("never lets a hand-edited blob write an unusable policy", async () => {
    globalThis.localStorage.setItem(
      LAYER_CACHE_POLICY_PERSIST_KEY,
      JSON.stringify({
        state: {
          overrides: {
            fire: { refreshMode: "manual", retainedDayLimit: 0 },
            "not-a-layer": { refreshMode: "manual" },
            watersheds: { refreshMode: "whenever" },
          },
          refreshRequestedAt: { fire: -1, vegetation: 1_700_000_000_000 },
          lastFetchedAt: { fire: "yesterday" },
        },
        version: 1,
      })
    );

    vi.resetModules();
    const reloaded = await import("@/lib/cache/layer-cache-policy-store");
    const state = reloaded.useLayerCachePolicyStore.getState();

    // 0 would evict the layer's whole cache on every write.
    expect(reloaded.layerCachePolicyFor("fire").retainedDayLimit).toBe(MIN_RETAINED_DAY_LIMIT);
    expect(Object.keys(state.overrides)).not.toContain("not-a-layer");
    // An override with nothing valid left in it is the same fact as no override at all.
    expect(state.overrides.watersheds).toBeUndefined();
    expect(reloaded.layerCachePolicyFor("watersheds").refreshMode).toBe("manual");
    expect(state.refreshRequestedAt.fire).toBeUndefined();
    expect(state.refreshRequestedAt.vegetation).toBe(1_700_000_000_000);
    expect(state.lastFetchedAt.fire).toBeUndefined();

    reloaded.resetLayerCachePolicyForTests();
  });

  it("sanitizes both shapes the same way in isolation", () => {
    expect(sanitizeLayerCacheOverrides(null)).toEqual({});
    expect(sanitizeLayerCacheOverrides({ fire: 3 })).toEqual({});
    expect(sanitizeLayerCacheOverrides({ fire: { retainedDayLimit: null } })).toEqual({
      fire: { retainedDayLimit: null },
    });
    expect(sanitizeLayerTimestamps({ fire: 5, bogus: 5, drought: 0 })).toEqual({ fire: 5 });
  });
});

describe("refresh requests", () => {
  it("supersedes only the entries created before the request", () => {
    const before = Date.now() - 5_000;
    const at = requestLayerRefresh("drought");

    expect(isSupersededByRefreshRequest("drought", before)).toBe(true);
    // An entry written in the same millisecond as the request IS the answer it asked for.
    expect(isSupersededByRefreshRequest("drought", at)).toBe(false);
    expect(isSupersededByRefreshRequest("drought", at + 1)).toBe(false);
    // Per layer, not global.
    expect(isSupersededByRefreshRequest("vegetation", before)).toBe(false);
    // An unattributable entry can never be superseded; there is no layer to ask for.
    expect(isSupersededByRefreshRequest(null, before)).toBe(false);
  });
});
