/**
 * Minimal hand-rolled typed IndexedDB wrapper: open/get/set/delete/iterate on one object
 * store. See src/lib/cache/AGENTS.md "why hand-rolled" for the rationale.
 *
 * Every exported function resolves to a safe default (`null`, `false`, `[]`) instead of
 * throwing, whatever goes wrong -- no IndexedDB, a blocked/aborted transaction, a quota
 * error, a closed connection. Callers never need their own try/catch around these.
 */

/** Which database and object store a call touches. One store per config, kept intentionally simple. */
export interface IndexedDbStoreConfig {
  databaseName: string;
  storeName: string;
}

/** True when a same-origin IndexedDB is reachable; false in SSR, jsdom, and most private-mode browsers. */
export function isIndexedDbAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

/** Opens (and lazily creates) the database and its one object store. Never throws. */
function openDatabase(config: IndexedDbStoreConfig): Promise<IDBDatabase | null> {
  if (!isIndexedDbAvailable()) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      const request = indexedDB.open(config.databaseName);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(config.storeName)) {
          db.createObjectStore(config.storeName);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** Runs one request against the store inside its own transaction; resolves `null` on any failure. */
function runTransaction<R>(
  config: IndexedDbStoreConfig,
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<R>
): Promise<R | null> {
  return openDatabase(config).then(
    (db) =>
      new Promise<R | null>((resolve) => {
        if (db === null) {
          resolve(null);
          return;
        }
        let settled = false;
        const settle = (value: R | null) => {
          if (settled) return;
          settled = true;
          resolve(value);
        };
        try {
          const transaction = db.transaction(config.storeName, mode);
          transaction.onerror = () => settle(null);
          transaction.onabort = () => settle(null);
          transaction.oncomplete = () => {
            try {
              db.close();
            } catch {
              // Already closed by the environment; nothing to clean up.
            }
          };
          const request = operation(transaction.objectStore(config.storeName));
          request.onsuccess = () => settle((request.result ?? null) as R | null);
          request.onerror = () => settle(null);
        } catch {
          settle(null);
        }
      })
  );
}

/** Reads one value by key, or `null` on a miss or any failure. */
export function getEntry<T>(config: IndexedDbStoreConfig, key: string): Promise<T | null> {
  return runTransaction<T>(config, "readonly", (store) => store.get(key) as IDBRequest<T>);
}

/** Writes one value by key. Resolves `true` on success, `false` on any failure (quota, blocked, etc). */
export async function setEntry<T>(
  config: IndexedDbStoreConfig,
  key: string,
  value: T
): Promise<boolean> {
  const result = await runTransaction<IDBValidKey>(config, "readwrite", (store) =>
    store.put(value, key)
  );
  return result !== null;
}

/** Deletes one value by key. A no-op, not an error, when the key or the store is absent. */
export async function deleteEntry(config: IndexedDbStoreConfig, key: string): Promise<void> {
  await runTransaction<undefined>(config, "readwrite", (store) => store.delete(key));
}

/** All stored values, in no particular order. Empty array on a failure, never a throw. */
export async function getAllEntries<T>(config: IndexedDbStoreConfig): Promise<T[]> {
  const result = await runTransaction<T[]>(config, "readonly", (store) => store.getAll() as IDBRequest<T[]>);
  return result ?? [];
}
