import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { QueryFunctionContext } from "@tanstack/react-query";
import fixture from "../../../../services/agri-data-service/src/agri_data_service/foundation/region/custom_colorado.fixture.json";
import { createIndexedDbLayerQueryPersister, scanSyncedDays, clearLayerEntries, STORE_CONFIG, CACHE_SCHEMA_VERSION, resetCacheAccounting, resetGenerationPinsForTests, revalidateAgainstDW, type StoredLayerQueryEntry } from "@/lib/cache/query-persister";
import { getAllEntries, setEntry } from "@/lib/cache/indexeddb-store";
import * as storage from "@/lib/cache/indexeddb-store";
import { regionCacheScope, scopedQueryKey } from "@/lib/cache/region-cache-scope";
import { createFakeIndexedDb } from "./fake-indexeddb";

const original = globalThis.indexedDB;
const persister = createIndexedDbLayerQueryPersister(() => null);
const context = {} as QueryFunctionContext;
const key = [["environmental", "getVegetationIndex"], { input: { date: "2020-01-01", bbox: "0,0,1,1" }, type: "query" }];
const query = { queryKey: key, queryHash: JSON.stringify(key) } as unknown as Parameters<typeof persister>[2];
function select(west = -108) {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", fixture.slug);
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify({ ...fixture, envelope: { ...fixture.envelope, west } }));
}
beforeEach(() => { globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory; resetCacheAccounting(); resetGenerationPinsForTests(); select(); });
afterEach(() => { globalThis.indexedDB = original; vi.unstubAllEnvs(); vi.restoreAllMocks(); });

it("isolates same-slug payloads and synced days while retaining other scopes", async () => {
  await persister(vi.fn().mockResolvedValue({ features: ["first"] }), context, query);
  const firstScope = regionCacheScope();
  select(-109);
  expect(regionCacheScope()).not.toBe(firstScope);
  expect((await scanSyncedDays())?.index.size).toBe(0);
  const fetch = vi.fn().mockResolvedValue({ features: ["second"] });
  await expect(persister(fetch, context, query)).resolves.toEqual({ features: ["second"] });
  expect(fetch).toHaveBeenCalledOnce();
  expect(await getAllEntries(STORE_CONFIG)).toHaveLength(2);
  await clearLayerEntries("vegetation");
  expect(await getAllEntries(STORE_CONFIG)).toHaveLength(1);
});

it("does not adopt unscoped legacy payloads for a custom region", async () => {
  await setEntry(STORE_CONFIG, query.queryHash, { key: query.queryHash, schemaVersion: CACHE_SCHEMA_VERSION,
    createdAt: Date.now(), expiresAt: Date.now() + 60000, approxByteSize: 10, value: { features: ["legacy"] } });
  const fetch = vi.fn().mockResolvedValue({ features: ["fresh"] });
  await expect(persister(fetch, context, query)).resolves.toEqual({ features: ["fresh"] });
  expect(fetch).toHaveBeenCalledOnce();
  expect(scopedQueryKey(query.queryHash)).not.toBe(query.queryHash);
});

it("rejects an old scope's late fetch without persisting it", async () => {
  let finish!: (value: unknown) => void;
  let started!: () => void;
  const begun = new Promise<void>((resolve) => { started = resolve; });
  const request = persister(() => { started(); return new Promise((resolve) => { finish = resolve; }); }, context, query);
  await begun;
  select(-109);
  finish({ features: ["old"] });
  await expect(request).rejects.toThrow("region_cache_scope_changed");
  expect(await getAllEntries(STORE_CONFIG)).toHaveLength(0);
});

it("does not clear the new scope when an old-scope clear waits behind a scan", async () => {
  await persister(vi.fn().mockResolvedValue({ features: ["first"] }), context, query);
  select(-109);
  await persister(vi.fn().mockResolvedValue({ features: ["second"] }), context, query);
  select();
  let release!: () => void;
  let entered!: () => void;
  const begun = new Promise<void>((resolve) => { entered = resolve; });
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const originalMetadata = storage.getAllMetadata;
  vi.spyOn(storage, "getAllMetadata").mockImplementationOnce(async (config) => {
    entered();
    await gate;
    return originalMetadata(config);
  });
  const scan = scanSyncedDays();
  await begun;
  const clear = clearLayerEntries("vegetation");
  select(-109);
  release();
  await scan;
  expect(await clear).toBeNull();
  expect(await getAllEntries(STORE_CONFIG)).toHaveLength(2);
});

it("rejects a cold result when scope changes during persistence", async () => {
  let entered!: () => void;
  let release!: () => void;
  const begun = new Promise<void>((resolve) => { entered = resolve; });
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const originalPut = storage.putEntryWithMetadata;
  vi.spyOn(storage, "putEntryWithMetadata").mockImplementationOnce(async (...args) => {
    const result = await originalPut(...args);
    entered();
    await gate;
    return result;
  });
  const request = persister(vi.fn().mockResolvedValue({ features: ["old"] }), context, query);
  await begun;
  select(-109);
  release();
  await expect(request).rejects.toThrow("region_cache_scope_changed");
  expect((await scanSyncedDays())?.index.size).toBe(0);
});

it("does not publish an old SWR generation pin after a delayed IDB lookup", async () => {
  const soilKey = [["environmental", "getSoilSurvey"], { input: { bbox: "0,0,1,1" }, type: "query" }];
  const hash = JSON.stringify(soilKey);
  const oldKey = scopedQueryKey(hash);
  const now = Date.now();
  const old: StoredLayerQueryEntry = { key: oldKey, schemaVersion: CACHE_SCHEMA_VERSION,
    createdAt: now, expiresAt: now + 60000, approxByteSize: 50,
    value: { availability: "published", revision: "old" }, layerId: "soil-survey" };
  await setEntry(STORE_CONFIG, oldKey, old);
  select(-109);
  const currentValue = { availability: "published", revision: "current" };
  const newKey = scopedQueryKey(hash);
  await setEntry(STORE_CONFIG, newKey, { ...old, key: newKey, value: currentValue });
  select();
  let entered!: () => void;
  let release!: () => void;
  const begun = new Promise<void>((resolve) => { entered = resolve; });
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const originalGet = storage.getEntry;
  vi.spyOn(storage, "getEntry").mockImplementationOnce(async (...args) => {
    const result = await originalGet(...args);
    entered();
    await gate;
    return result;
  });
  const revalidation = revalidateAgainstDW(async () => ({ availability: "published", revision: "late-old" }),
    context, soilKey, oldKey, old);
  await begun;
  select(-109);
  release();
  await revalidation;
  const network = vi.fn().mockRejectedValue(new Error("new-scope cached release must remain usable"));
  const soilQuery = { queryKey: soilKey, queryHash: hash } as unknown as Parameters<typeof persister>[2];
  await expect(persister(network, context, soilQuery)).resolves.toEqual(currentValue);
  expect(network).not.toHaveBeenCalled();
});
