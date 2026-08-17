/**
 * Minimal hand-rolled typed IndexedDB wrapper over TWO object stores: the payloads, and a
 * sibling metadata row per payload. See src/lib/cache/AGENTS.md "why hand-rolled" for the
 * rationale, and "the metadata store" for why the split exists.
 *
 * Every exported function resolves to a safe default (`null`, `false`, `[]`) instead of
 * throwing, whatever goes wrong -- no IndexedDB, a blocked/aborted transaction, a quota
 * error, a closed connection. Callers never need their own try/catch around these.
 */

/** Which database and object stores a call touches. */
export interface IndexedDbStoreConfig {
  databaseName: string;
  /** Holds the cached payloads: large, and never read by a sweep. */
  storeName: string;
  /** Holds one small row per payload: what every sweep reads instead of the payloads. */
  metadataStoreName: string;
  /** Bumped when a store is added; `onupgradeneeded` creates whatever is missing. */
  version: number;
}

/** True when a same-origin IndexedDB is reachable; false in SSR, jsdom, and most private-mode browsers. */
export function isIndexedDbAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

/** Opens (and lazily creates or upgrades) the database and its object stores. Never throws. */
function openDatabase(config: IndexedDbStoreConfig): Promise<IDBDatabase | null> {
  if (!isIndexedDbAvailable()) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      const request = indexedDB.open(config.databaseName, config.version);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(config.storeName)) {
          db.createObjectStore(config.storeName);
        }
        if (!db.objectStoreNames.contains(config.metadataStoreName)) {
          db.createObjectStore(config.metadataStoreName);
        }
      };
      request.onsuccess = () => {
        const db = request.result;
        // A connection this tab left open would block another tab's upgrade indefinitely.
        // We open and close per transaction anyway, so closing on request costs nothing.
        db.onversionchange = () => {
          try {
            db.close();
          } catch {
            // Already gone.
          }
        };
        resolve(db);
      };
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** Runs one request against one store inside its own transaction; resolves `null` on any failure. */
function runTransaction<R>(
  config: IndexedDbStoreConfig,
  storeName: string,
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
          const transaction = db.transaction(storeName, mode);
          transaction.onerror = () => settle(null);
          transaction.onabort = () => settle(null);
          transaction.oncomplete = () => {
            try {
              db.close();
            } catch {
              // Already closed by the environment; nothing to clean up.
            }
          };
          const request = operation(transaction.objectStore(storeName));
          request.onsuccess = () => settle((request.result ?? null) as R | null);
          request.onerror = () => settle(null);
        } catch {
          settle(null);
        }
      })
  );
}

/**
 * Runs `work` against both stores in ONE transaction and resolves whether it COMMITTED.
 *
 * The commit is what is reported, not the individual requests: IndexedDB transactions are
 * atomic, so "committed" is strictly more informative than a count of requests that appeared to
 * succeed before an abort rolled them back. A caller adjusting a running total needs exactly
 * this distinction -- see `query-persister.ts` "running totals".
 */
function runWriteTransaction(
  config: IndexedDbStoreConfig,
  storeNames: readonly string[],
  work: (stores: Record<string, IDBObjectStore>) => void
): Promise<boolean> {
  return openDatabase(config).then(
    (db) =>
      new Promise<boolean>((resolve) => {
        if (db === null) {
          resolve(false);
          return;
        }
        let settled = false;
        const settle = (committed: boolean) => {
          if (settled) return;
          settled = true;
          resolve(committed);
        };
        try {
          const transaction = db.transaction([...storeNames], "readwrite");
          transaction.onerror = () => settle(false);
          transaction.onabort = () => settle(false);
          transaction.oncomplete = () => {
            try {
              db.close();
            } catch {
              // Already closed by the environment; nothing to clean up.
            }
            settle(true);
          };
          const stores: Record<string, IDBObjectStore> = {};
          for (const name of storeNames) stores[name] = transaction.objectStore(name);
          work(stores);
        } catch {
          settle(false);
        }
      })
  );
}

/** Reads one payload by key, or `null` on a miss or any failure. */
export function getEntry<T>(config: IndexedDbStoreConfig, key: string): Promise<T | null> {
  return runTransaction<T>(config, config.storeName, "readonly", (store) => store.get(key) as IDBRequest<T>);
}

/**
 * Writes one payload by key, with no metadata row. Kept for tests and for seeding; production
 * writes go through `putEntryWithMetadata` so a payload never exists unindexed for long.
 */
export async function setEntry<T>(
  config: IndexedDbStoreConfig,
  key: string,
  value: T
): Promise<boolean> {
  const result = await runTransaction<IDBValidKey>(config, config.storeName, "readwrite", (store) =>
    store.put(value, key)
  );
  return result !== null;
}

/** Writes a payload and its metadata row atomically. `false` means nothing was committed. */
export function putEntryWithMetadata<T, M>(
  config: IndexedDbStoreConfig,
  key: string,
  value: T,
  metadata: M
): Promise<boolean> {
  return runWriteTransaction(config, [config.storeName, config.metadataStoreName], (stores) => {
    stores[config.storeName].put(value, key);
    stores[config.metadataStoreName].put(metadata, key);
  });
}

/** Writes only the metadata row -- what a recency bump costs, instead of rewriting the payload. */
export async function putMetadata<M>(
  config: IndexedDbStoreConfig,
  key: string,
  metadata: M
): Promise<boolean> {
  const result = await runTransaction<IDBValidKey>(
    config,
    config.metadataStoreName,
    "readwrite",
    (store) => store.put(metadata, key)
  );
  return result !== null;
}

/** Writes many metadata rows in ONE transaction; the one-time backfill's only write. */
export function putManyMetadata<M>(
  config: IndexedDbStoreConfig,
  rows: ReadonlyArray<{ key: string; metadata: M }>
): Promise<boolean> {
  if (rows.length === 0) return Promise.resolve(true);
  return runWriteTransaction(config, [config.metadataStoreName], (stores) => {
    for (const row of rows) stores[config.metadataStoreName].put(row.metadata, row.key);
  });
}

/** Deletes one key from both stores. A no-op, not an error, when the key is absent. */
export async function deleteEntry(config: IndexedDbStoreConfig, key: string): Promise<void> {
  await runWriteTransaction(config, [config.storeName, config.metadataStoreName], (stores) => {
    stores[config.storeName].delete(key);
    stores[config.metadataStoreName].delete(key);
  });
}

/** Deletes many keys from both stores in ONE transaction. `false` means nothing was committed. */
export async function deleteEntries(
  config: IndexedDbStoreConfig,
  keys: readonly string[]
): Promise<boolean> {
  if (keys.length === 0) return true;
  return runWriteTransaction(config, [config.storeName, config.metadataStoreName], (stores) => {
    for (const key of keys) {
      stores[config.storeName].delete(key);
      stores[config.metadataStoreName].delete(key);
    }
  });
}

/** All payload values. Empty array on a failure, never a throw. Tests and backfill only. */
export async function getAllEntries<T>(config: IndexedDbStoreConfig): Promise<T[]> {
  const result = await runTransaction<T[]>(config, config.storeName, "readonly", (store) =>
    store.getAll() as IDBRequest<T[]>
  );
  return result ?? [];
}

/**
 * Every payload KEY, without deserializing a single payload.
 *
 * `getAllKeys()` reads the store's key index only, so this costs the same whether the payloads
 * behind it total 5 MB or 500 MB. It is how a sweep learns what exists before touching anything.
 */
export async function getAllEntryKeys(config: IndexedDbStoreConfig): Promise<string[]> {
  const result = await runTransaction<IDBValidKey[]>(
    config,
    config.storeName,
    "readonly",
    (store) => store.getAllKeys() as IDBRequest<IDBValidKey[]>
  );
  return (result ?? []).map((key) => String(key));
}

/** Every metadata row. Small by construction -- this is the read a sweep is built around. */
export async function getAllMetadata<M>(config: IndexedDbStoreConfig): Promise<M[]> {
  const result = await runTransaction<M[]>(
    config,
    config.metadataStoreName,
    "readonly",
    (store) => store.getAll() as IDBRequest<M[]>
  );
  return result ?? [];
}

/**
 * Visits every stored PAYLOAD one at a time with a cursor. Resolves when the walk ends; never throws.
 *
 * Reserved for the one-time metadata backfill: a cursor holds one payload live at a time (so
 * peak memory is one entry however large the store), but it still structured-clone-deserializes
 * every value it passes, which is main-thread work proportional to the whole cache. Nothing on a
 * recurring path may use it -- see `getAllMetadata`/`getAllEntryKeys` above, and AGENTS.md
 * "the metadata store".
 */
export function forEachEntry<T>(
  config: IndexedDbStoreConfig,
  visit: (value: T, key: string) => void
): Promise<void> {
  return openDatabase(config).then(
    (db) =>
      new Promise<void>((resolve) => {
        if (db === null) {
          resolve();
          return;
        }
        let settled = false;
        const settle = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        try {
          const transaction = db.transaction(config.storeName, "readonly");
          transaction.onerror = () => settle();
          transaction.onabort = () => settle();
          transaction.oncomplete = () => {
            try {
              db.close();
            } catch {
              // Already closed by the environment; nothing to clean up.
            }
            settle();
          };
          const request = transaction.objectStore(config.storeName).openCursor();
          request.onerror = () => settle();
          request.onsuccess = () => {
            const cursor = request.result;
            if (cursor === null) {
              settle();
              return;
            }
            try {
              visit(cursor.value as T, String(cursor.key));
            } catch {
              // One unreadable row never aborts the walk.
            }
            cursor.continue();
          };
        } catch {
          settle();
        }
      })
  );
}
