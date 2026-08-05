import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  deleteEntry,
  getAllEntries,
  getEntry,
  isIndexedDbAvailable,
  setEntry,
  type IndexedDbStoreConfig,
} from "@/lib/cache/indexeddb-store";
import { createAlwaysFailingIndexedDb, createFakeIndexedDb } from "./fake-indexeddb";

const CONFIG: IndexedDbStoreConfig = {
  databaseName: "test-db",
  storeName: "test-store",
};

const originalIndexedDb = globalThis.indexedDB;

afterEach(() => {
  globalThis.indexedDB = originalIndexedDb;
});

describe("isIndexedDbAvailable", () => {
  it("is false in the plain jsdom test environment", () => {
    // jsdom does not implement IndexedDB at all; this is the SSR/vitest guard in production.
    // @ts-expect-error -- simulate the ambient absence explicitly rather than relying on jsdom.
    delete globalThis.indexedDB;
    expect(isIndexedDbAvailable()).toBe(false);
  });

  it("is true once a factory is installed", () => {
    globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
    expect(isIndexedDbAvailable()).toBe(true);
  });
});

describe("get/set/delete round trip", () => {
  beforeEach(() => {
    globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
  });

  it("returns null for a key that was never written", async () => {
    await expect(getEntry(CONFIG, "missing")).resolves.toBeNull();
  });

  it("writes then reads back the same value", async () => {
    const value = { hello: "world", count: 3 };
    await expect(setEntry(CONFIG, "k1", value)).resolves.toBe(true);
    await expect(getEntry(CONFIG, "k1")).resolves.toEqual(value);
  });

  it("deletes a value so a later read misses", async () => {
    await setEntry(CONFIG, "k1", { a: 1 });
    await deleteEntry(CONFIG, "k1");
    await expect(getEntry(CONFIG, "k1")).resolves.toBeNull();
  });

  it("deleting an absent key is a harmless no-op", async () => {
    await expect(deleteEntry(CONFIG, "never-existed")).resolves.toBeUndefined();
  });

  it("getAllEntries returns every stored value", async () => {
    await setEntry(CONFIG, "k1", { n: 1 });
    await setEntry(CONFIG, "k2", { n: 2 });
    const all = await getAllEntries<{ n: number }>(CONFIG);
    expect(all.map((entry) => entry.n).sort()).toEqual([1, 2]);
  });

  it("survives a fresh open() call, the way a reload would", async () => {
    await setEntry(CONFIG, "durable", { survives: true });
    // A brand new call re-opens the database from scratch; the fake keeps its Map
    // per database name across `open()` calls, exactly like a browser keeps IndexedDB
    // across page loads.
    await expect(getEntry(CONFIG, "durable")).resolves.toEqual({ survives: true });
  });
});

describe("degradation", () => {
  it("get/set/delete/getAllEntries all resolve to safe defaults with no indexedDB at all", async () => {
    // @ts-expect-error -- simulate the ambient absence explicitly.
    delete globalThis.indexedDB;
    await expect(getEntry(CONFIG, "k")).resolves.toBeNull();
    await expect(setEntry(CONFIG, "k", { a: 1 })).resolves.toBe(false);
    await expect(deleteEntry(CONFIG, "k")).resolves.toBeUndefined();
    await expect(getAllEntries(CONFIG)).resolves.toEqual([]);
  });

  it("resolves safe defaults, never throws, when indexedDB.open always errors", async () => {
    globalThis.indexedDB = createAlwaysFailingIndexedDb() as unknown as IDBFactory;
    await expect(getEntry(CONFIG, "k")).resolves.toBeNull();
    await expect(setEntry(CONFIG, "k", { a: 1 })).resolves.toBe(false);
  });
});
