import * as store from "@/lib/cache/indexeddb-store";
import { scopedQueryKey, unscopedQueryHash } from "@/lib/cache/region-cache-scope";
import type { IndexedDbStoreConfig } from "@/lib/cache/indexeddb-store";

/** Policy fixtures use query hashes; storage receives the current deployment namespace. */
export function setEntry<T>(config: IndexedDbStoreConfig, hash: string, value: T): Promise<boolean> {
  const key = scopedQueryKey(hash);
  const scopedValue = value !== null && typeof value === "object" && "key" in value ? { ...value, key } : value;
  return store.setEntry(config, key, scopedValue);
}
export function getEntry<T>(config: IndexedDbStoreConfig, hash: string): Promise<T | null> {
  return store.getEntry<T>(config, scopedQueryKey(hash));
}
export async function getAllEntries<T>(config: IndexedDbStoreConfig): Promise<T[]> {
  const values = await store.getAllEntries<T>(config);
  return values.map((value) => value !== null && typeof value === "object" && "key" in value && typeof value.key === "string"
    ? { ...value, key: unscopedQueryHash(value.key) } : value);
}
export async function getAllEntryKeys(config: IndexedDbStoreConfig): Promise<string[]> {
  return (await store.getAllEntryKeys(config)).map(unscopedQueryHash);
}
